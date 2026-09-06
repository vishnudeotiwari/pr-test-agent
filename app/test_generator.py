from __future__ import annotations

import json
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)


class TestGenerator:
    def __init__(self, model: str, ollama_url: str = "http://localhost:11434/api/chat", timeout: int | None = None):
        self.model = model
        self.ollama_url = ollama_url
        self.timeout = timeout if timeout is not None else int(os.getenv("OLLAMA_TIMEOUT", "600"))

    def _call_ollama(self, system_prompt: str, user_prompt: str) -> str:
        logger.info("Calling Ollama model '%s' (timeout=%ss)...", self.model, self.timeout)
        try:
            response = requests.post(
            self.ollama_url,
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0, "num_predict": 4096},
            },
            timeout=self.timeout,
        )
            response.raise_for_status()
            return response.json()["message"]["content"]
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"Ollama did not respond within {self.timeout} seconds. "
                f"Model={self.model}, URL={self.ollama_url}. "
                "Check `ollama ps` and consider increasing OLLAMA_TIMEOUT."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                f"Could not call Ollama at {self.ollama_url}: {exc}"
            ) from exc

    @staticmethod
    def _extract_json(text: str) -> dict:
        text = text.strip()
        for candidate in (
            text,
            re.sub(r"^```(?:json)?\s*|\s*```$", "", text),
        ):
            try:
                value = json.loads(candidate)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                pass

        start = text.find("{")
        if start < 0:
            raise ValueError("Ollama did not return a JSON object.")

        depth = 0
        in_string = False
        escaped = False

        for i in range(start, len(text)):
            ch = text[i]

            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    value = json.loads(text[start:i + 1])
                    if isinstance(value, dict):
                        return value

        raise ValueError("Could not extract a valid JSON object from Ollama response.")

    @staticmethod
    def _find_matching_brace(source: str, opening: int) -> int:
        depth = 0
        in_string = False
        escaped = False
        line_comment = False
        block_comment = False
        i = opening

        while i < len(source):
            ch = source[i]
            nxt = source[i + 1] if i + 1 < len(source) else ""

            if line_comment:
                if ch == "\n":
                    line_comment = False
            elif block_comment:
                if ch == "*" and nxt == "/":
                    block_comment = False
                    i += 1
            elif in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == "/" and nxt == "/":
                    line_comment = True
                    i += 1
                elif ch == "/" and nxt == "*":
                    block_comment = True
                    i += 1
                elif ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return i
            i += 1

        raise ValueError("Could not find matching Java brace.")

    @classmethod
    def _extract_test_methods(cls, source: str) -> list[dict]:
        pattern = re.compile(
            r"(?m)^[ \t]*@Test\b[^\n]*\n"
            r"(?:[ \t]*@[^\n]*\n)*"
            r"[ \t]*(?:(?:public|protected|private)\s+)?"
            r"(?:static\s+)?(?:final\s+)?"
            r"(?:[\w<>\[\], ?]+\s+)?"
            r"(?P<name>test\w+)\s*\([^)]*\)"
            r"(?:\s*throws\s+[^{]+)?\s*\{"
        )

        result = []
        for match in pattern.finditer(source):
            opening = source.find("{", match.start(), match.end())
            closing = cls._find_matching_brace(source, opening)
            result.append({
                "method_name": match.group("name"),
                "start": match.start(),
                "end": closing + 1,
                "source": source[match.start():closing + 1],
            })
        return result

    @staticmethod
    def _normalize_test_path(path: str, known_paths: set[str]) -> str:
        """
        Normalize paths returned by the LLM to the canonical repository path.

        Accepted forms include:
          src/test/java/com/example/FooTest.java
          com/example/FooTest.java
          com.example.FooTest.java
          FooTest.java (when there is exactly one known matching class)
        """
        path = str(path or "").strip().replace("\\", "/")
        if not path:
            return ""

        if path in known_paths:
            return path

        if path.startswith("src/test/java/"):
            return path

        # Fully-qualified Java class name, e.g.
        # com.example.blogging.controller.EmployeeControllerTest.java
        if "/" not in path and "." in path and path.endswith(".java"):
            candidate = "src/test/java/" + path[:-5].replace(".", "/") + ".java"
            if candidate in known_paths:
                return candidate

        # Java source path without the src/test/java prefix.
        if path.endswith(".java") and "/" in path:
            candidate = "src/test/java/" + path.lstrip("/")
            if candidate in known_paths:
                return candidate

        # Bare class name.
        if "/" not in path and path.endswith(".java"):
            matches = [p for p in known_paths if p.endswith("/" + path)]
            if len(matches) == 1:
                return matches[0]

        return path

    @staticmethod
    def _normalize_file_entries(
        files_input,
        label: str,
    ) -> list[dict]:
        """
        Accept both the dictionary representation used by Stage 4 and the
        older list-of-dicts representation.
        """
        if not files_input:
            return []

        if isinstance(files_input, dict):
            entries = [
                {"file_path": path, "content": content}
                for path, content in files_input.items()
            ]
        elif isinstance(files_input, list):
            entries = files_input
        else:
            raise ValueError(f"{label} must be a dict or list.")

        normalized = []
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError(f"Each {label} entry must be an object.")

            path = (
                item.get("file_path")
                or item.get("path")
                or item.get("file")
                or item.get("filename")
            )
            content = (
                item.get("content")
                if item.get("content") is not None
                else item.get("source")
            )
            if content is None:
                content = item.get("code")

            if not path or content is None:
                raise ValueError(
                    f"Each {label} entry must contain a file path and content."
                )

            normalized.append(
                {
                    "file_path": str(path).replace("\\", "/"),
                    "content": str(content),
                }
            )

        return normalized


    def generate(
        self,
        analysis: dict,
        source_files: dict[str, str] | list[dict],
        existing_tests: dict[str, str] | list[dict] | None = None,
    ) -> list[dict]:
        """Generate complete JUnit 5 test files from Stage 2 analysis and source."""
        source_entries = self._normalize_file_entries(source_files, "production source files")
        existing_entries = self._normalize_file_entries(existing_tests, "existing test files") if existing_tests else []
        if not source_entries:
            raise ValueError("No production source files were supplied.")

        system_prompt = """
You generate Java JUnit 5 tests for Spring Boot applications.

Return JSON only in this exact shape:
{
  "tests": [
    {
      "file_path": "src/test/java/com/example/.../SomeControllerTest.java",
      "class_name": "SomeControllerTest",
      "reason": "Why these tests cover the changed behavior",
      "content": "complete Java source file"
    }
  ]
}

Rules:
- Generate complete compilable JUnit 5 test source files.
- Use Mockito and Spring MockMvc only when appropriate to the actual production code.
- Prefer unit/controller tests that match the existing project style.
- Test the behaviors explicitly identified by Stage 2.
- Use exact endpoint paths, HTTP methods, fields, IDs, status codes, and response values from the supplied source and analysis.
- Do not invent APIs, fields, constructors, IDs, database behavior, or response values.
- Do not modify production code, pom.xml, or application configuration.
- If an existing test file is supplied, preserve its conventions and avoid duplicating tests unnecessarily.
- Every returned file_path must start with src/test/java/ and end with .java.
- content must contain a package declaration matching the production package structure.
- Return only the JSON object; no Markdown fences or explanatory text.
"""

        analysis_text = json.dumps(analysis, indent=2)
        source_text = "\n\n".join(
            f"FILE: {item['file_path']}\n{item['content']}" for item in source_entries
        )
        existing_text = "(none)" if not existing_entries else "\n\n".join(
            f"FILE: {item['file_path']}\n{item['content']}" for item in existing_entries
        )

        user_prompt = (
            "STAGE 2 ANALYSIS:\n\n" + analysis_text
            + "\n\nPRODUCTION SOURCE FILES:\n\n" + source_text
            + "\n\nEXISTING TEST FILES:\n\n" + existing_text
            + "\n\nGenerate the smallest complete set of high-value JUnit 5 tests needed for the changed production code."
        )

        last_error = None
        for attempt in range(1, 3):
            try:
                data = self._extract_json(
                    self._call_ollama(system_prompt, user_prompt)
                )
                break
            except RuntimeError as exc:
                last_error = exc
                if attempt == 2:
                    raise
                logger.warning(
                    "Ollama call failed on attempt %d/2: %s. Retrying...",
                    attempt,
                    exc,
                )
        else:
            raise last_error or RuntimeError("Ollama generation failed.")
        tests = data.get("tests")
        if not isinstance(tests, list) or not tests:
            raise ValueError("Ollama response must contain a non-empty tests list.")

        result = []
        known_paths = {item["file_path"] for item in existing_entries}
        for item in tests:
            if not isinstance(item, dict):
                raise ValueError("Each generated test must be an object.")
            raw_path = item.get("file_path") or item.get("path") or item.get("filename")
            path = self._normalize_test_path(raw_path, known_paths) if raw_path else ""
            if not path.startswith("src/test/java/") or not path.endswith(".java"):
                raise ValueError(f"Invalid generated test path: {raw_path}")
            class_name = item.get("class_name") or Path(path).stem
            content = item.get("content") or item.get("source") or item.get("code")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"Generated test {path} has no content.")
            if not re.search(r"(?m)^\s*package\s+[\w.]+\s*;", content):
                raise ValueError(f"Generated test {path} has no package declaration.")
            if not re.search(r"\bclass\s+" + re.escape(class_name) + r"\b", content):
                raise ValueError(f"Generated test {path} does not declare class {class_name}.")
            if "@Test" not in content:
                raise ValueError(f"Generated test {path} contains no @Test method.")
            result.append({
                "file_path": path,
                "class_name": str(class_name),
                "reason": str(item.get("reason", "")),
                "content": content,
            })

        return result

    def write_tests(self, repo_dir, tests: list[dict]) -> list[str]:
        """Write generated test files into the repository and return paths."""
        written = []
        for test in tests:
            path = repo_dir / test["file_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(test["content"], encoding="utf-8")
            written.append(test["file_path"])
            logger.info("Wrote generated test: %s", test["file_path"])
        return written

    def repair_tests(
        self,
        generated_tests: list[dict] | dict[str, str] | None = None,
        maven_output: str = "",
        analysis: dict | None = None,
        source_files: list[dict] | dict[str, str] | None = None,
        test_files: list[dict] | dict[str, str] | None = None,
    ) -> list[dict]:
        if generated_tests is None:
            generated_tests = test_files

        if generated_tests is None:
            raise ValueError("No generated test files were supplied.")

        files = self._normalize_file_entries(generated_tests, "generated test files")
        if not files:
            raise ValueError("No generated test files were supplied.")

        known_paths = {f["file_path"] for f in files}

        for file_entry in files:
            file_entry["methods"] = [
                m["method_name"]
                for m in self._extract_test_methods(file_entry["content"])
            ]

        source_entries = self._normalize_file_entries(
            source_files,
            "production source files",
        )

        system_prompt = """
You repair Java JUnit 5 tests.

Return JSON only in this exact shape:
{
  "method_repairs": [
    {
      "file_path": "src/test/java/...",
      "method_name": "existingTestMethodName",
      "method": "@Test\\nvoid existingTestMethodName() throws Exception { ... }"
    }
  ]
}

Rules:
- Repair only methods implicated by the Maven failure.
- Return a COMPLETE replacement method, including @Test.
- method_name must exactly match an existing test method.
- The file_path MUST be the exact repository path shown under CURRENT TEST FILES.
- Never return a Java package name or fully-qualified class name as file_path.
- Do NOT return old/new text replacements.
- Do NOT add, remove, rename, or reorder tests.
- Do NOT change package declarations, imports, production code, pom.xml, or unrelated methods.
- Use the actual test source, production source, Stage 2 analysis, and Maven error.
- Do not invent endpoints, fields, IDs, or response values.
- Return at least one repair only when a repair is actually needed.
"""

        analysis_text = (
            json.dumps(analysis, indent=2)
            if analysis
            else "(no Stage 2 analysis provided)"
        )

        source_text = "(no production source files provided)"
        if source_entries:
            source_parts = []
            for item in source_entries:
                source_parts.append(
                    f"FILE: {item['file_path']}\n{item['content']}"
                )
            source_text = "\n\n".join(source_parts)

        user_prompt = (
            "STAGE 2 ANALYSIS:\n\n"
            + analysis_text
            + "\n\nPRODUCTION SOURCE FILES:\n\n"
            + source_text
            + "\n\nCURRENT TEST FILES:\n\n"
            + "\n\n".join(
                f"FILE: {f['file_path']}\n"
                f"EXISTING TEST METHODS: {f['methods']}\n"
                f"{f['content']}"
                for f in files
            )
            + "\n\nMAVEN FAILURE OUTPUT:\n"
            + maven_output
            + "\n\nReturn only method_repairs JSON."
        )

        data = self._extract_json(
            self._call_ollama(system_prompt, user_prompt)
        )
        repairs = data.get("method_repairs")

        if not isinstance(repairs, list) or not repairs:
            raise ValueError(
                "Ollama response must contain a non-empty method_repairs list."
            )

        result = [
            {"file_path": f["file_path"], "content": f["content"]}
            for f in files
        ]
        by_path = {x["file_path"]: x for x in result}

        for repair in repairs:
            if not isinstance(repair, dict):
                raise ValueError("Each method repair must be an object.")

            raw_path = repair.get("file_path", "")
            path = self._normalize_test_path(raw_path, known_paths)

            name = repair.get("method_name")
            method = repair.get("method")

            # If the model omitted method_name, infer it from the returned
            # method body. This keeps the repair strict while handling a
            # common small-model JSON omission.
            if (not isinstance(name, str) or not name.strip()) and isinstance(method, str):
                method_match = re.search(
                    r"\b(?:void|[\w<>\[\], ?]+)\s+(test\w+)\s*\(",
                    method,
                )
                if method_match:
                    name = method_match.group(1)

            if path not in by_path:
                raise ValueError(f"Unknown test file: {path}")

            if not isinstance(name, str) or not name.strip():
                raise ValueError("Repair is missing method_name.")

            if not isinstance(method, str) or not method.strip():
                raise ValueError(f"Repair for {name} is missing method.")

            if not re.search(r"(?m)^\s*@Test\b", method):
                raise ValueError(f"Repair for {name} does not contain @Test.")

            if not re.search(rf"\b{re.escape(name)}\s*\(", method):
                raise ValueError(
                    f"Repair does not contain expected method name: {name}"
                )

            source = by_path[path]["content"]
            matches = [
                m
                for m in self._extract_test_methods(source)
                if m["method_name"] == name
            ]

            if len(matches) != 1:
                raise ValueError(
                    f"Expected exactly one method named {name} in {path}, "
                    f"found {len(matches)}."
                )

            target = matches[0]
            by_path[path]["content"] = (
                source[:target["start"]]
                + method.strip()
                + source[target["end"]:]
            )
            logger.info("Applied method repair: %s -> %s", path, name)

        # Safety: test count, names/order, and package declaration must be unchanged.
        for original in files:
            repaired = by_path[original["file_path"]]["content"]
            old_methods = self._extract_test_methods(original["content"])
            new_methods = self._extract_test_methods(repaired)

            old_names = [m["method_name"] for m in old_methods]
            new_names = [m["method_name"] for m in new_methods]

            if old_names != new_names:
                raise ValueError(
                    f"Test methods changed in {original['file_path']}: "
                    f"{old_names} -> {new_names}"
                )

            old_pkg = re.search(
                r"(?m)^\s*package\s+[\w.]+\s*;",
                original["content"],
            )
            new_pkg = re.search(
                r"(?m)^\s*package\s+[\w.]+\s*;",
                repaired,
            )

            if (
                (old_pkg is None) != (new_pkg is None)
                or (
                    old_pkg
                    and new_pkg
                    and old_pkg.group(0) != new_pkg.group(0)
                )
            ):
                raise ValueError(
                    f"Package declaration changed in "
                    f"{original['file_path']}"
                )

        return result



# v3 compatibility: accepts Stage 4's optional analysis argument; method-only repair is used.
