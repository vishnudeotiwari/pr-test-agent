import argparse
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from app.config import get_settings
from app.github_client import GitHubClient
from app.test_generator import TestGenerator


MAX_REPAIR_ATTEMPTS = 3
MAVEN_TIMEOUT_SECONDS = 600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def find_generated_tests(generated_tests_file: Path) -> list[dict]:
    if not generated_tests_file.exists():
        raise FileNotFoundError(
            f"Generated tests file does not exist: {generated_tests_file}"
        )

    data = json.loads(generated_tests_file.read_text(encoding="utf-8"))

    if isinstance(data, list):
        tests = data
    elif isinstance(data, dict):
        tests = data.get("tests")
    else:
        raise ValueError("Generated tests JSON must be a list or contain a 'tests' list.")

    if not isinstance(tests, list):
        raise ValueError("Generated tests JSON does not contain a valid tests list.")

    result = []
    for test in tests:
        if not isinstance(test, dict):
            continue

        file_path = test.get("file_path") or test.get("path") or test.get("file")
        content = test.get("content")

        if not file_path or not content:
            continue

        path = Path(file_path)
        if not file_path.startswith("src/test/java/"):
            raise ValueError(f"Generated test is outside src/test/java: {file_path}")
        if ".." in path.parts:
            raise ValueError(f"Unsafe generated test path: {file_path}")
        if path.suffix != ".java":
            raise ValueError(f"Generated test is not Java: {file_path}")

        result.append({"file_path": file_path, "content": content})

    if not result:
        raise RuntimeError(f"No generated Java test files were found in {generated_tests_file}")

    return result


def write_generated_tests(repo_dir: Path, generated_tests: list[dict]) -> None:
    for test in generated_tests:
        file_path = test["file_path"]
        target = repo_dir / file_path

        if not file_path.startswith("src/test/java/") or ".." in Path(file_path).parts:
            raise ValueError(f"Refusing to write unsafe test path: {file_path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(test["content"], encoding="utf-8")
        logging.info("Wrote generated test: %s", file_path)


def read_generated_test_files(repo_dir: Path, generated_tests: list[dict]) -> dict[str, str]:
    result = {}

    for test in generated_tests:
        file_path = test["file_path"]
        target = repo_dir / file_path

        if not target.exists():
            raise RuntimeError(f"Generated test was not written successfully: {target}")

        result[file_path] = target.read_text(encoding="utf-8")

    return result


def find_changed_java_source_files(
    repo_dir: Path,
    changed_files: list[dict],
) -> dict[str, str]:
    source_files = {}

    for changed_file in changed_files:
        file_path = (
            changed_file.get("filename")
            or changed_file.get("file")
            or changed_file.get("path")
        )

        if not file_path or not file_path.startswith("src/main/java/"):
            continue

        if not file_path.endswith(".java"):
            continue

        target = repo_dir / file_path
        if target.exists():
            source_files[file_path] = target.read_text(encoding="utf-8")

    return source_files


def load_analysis(analysis_file: Path) -> dict:
    if not analysis_file.exists():
        logging.warning("Analysis file not found: %s", analysis_file)
        return {}

    try:
        return json.loads(analysis_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid analysis JSON: {analysis_file}") from exc


def run_maven_tests(repo_dir: Path) -> tuple[bool, str]:
    if os.name == "nt":
        maven_command = "mvn.cmd"
    else:
        maven_command = "mvn"

    maven_home = os.getenv("MAVEN_HOME")
    if maven_home:
        candidate = Path(maven_home) / "bin" / ("mvn.cmd" if os.name == "nt" else "mvn")
        if candidate.exists():
            maven_command = str(candidate)

    logging.info("Running Maven tests in %s", repo_dir)

    try:
        completed = subprocess.run(
            [maven_command, "test"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=MAVEN_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part for part in [exc.stdout, exc.stderr] if part
        )
        return False, f"Maven test command timed out.\n{output}"

    output = "\n".join(
        part for part in [completed.stdout, completed.stderr] if part
    )

    logging.info("Maven exit code: %s", completed.returncode)

    if completed.returncode == 0:
        return True, output

    return False, output


def count_tests(content: str) -> int:
    return len(re.findall(r"(?m)^\s*@Test\b", content))


def validate_repaired_tests(
    original_tests: dict[str, str],
    candidate_tests: dict[str, str],
) -> None:
    original_paths = set(original_tests)
    candidate_paths = set(candidate_tests)

    if original_paths != candidate_paths:
        raise ValueError(
            "Repair changed the set of test files. "
            f"Original={sorted(original_paths)}, candidate={sorted(candidate_paths)}"
        )

    for file_path, original in original_tests.items():
        candidate = candidate_tests[file_path]

        if not candidate.strip():
            raise ValueError(f"Repair produced empty content: {file_path}")

        if not file_path.startswith("src/test/java/") or ".." in Path(file_path).parts:
            raise ValueError(f"Unsafe repaired test path: {file_path}")

        package_match = re.search(r"(?m)^\s*package\s+([A-Za-z_][\w.]*)\s*;", candidate)
        if not package_match:
            raise ValueError(f"Repaired test has no package declaration: {file_path}")

        expected_package = ".".join(Path(file_path).parts[3:-1])
        if package_match.group(1) != expected_package:
            raise ValueError(
                f"Package mismatch in {file_path}: "
                f"expected {expected_package}, got {package_match.group(1)}"
            )

        before = count_tests(original)
        after = count_tests(candidate)

        if after != before:
            raise ValueError(
                f"Repair changed @Test count in {file_path}: {before} -> {after}"
            )

        if "```" in candidate:
            raise ValueError(f"Markdown fence found in repaired Java source: {file_path}")


def write_repaired_tests(repo_dir: Path, repaired_tests: dict[str, str]) -> None:
    for file_path, content in repaired_tests.items():
        if not file_path.startswith("src/test/java/") or ".." in Path(file_path).parts:
            raise ValueError(f"Unsafe repaired test path: {file_path}")

        target = repo_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        logging.info("Applied repair: %s", file_path)


def apply_deterministic_repairs(
    test_files: dict[str, str],
) -> dict[str, str]:
    repaired = {}

    for file_path, content in test_files.items():
        new_content = re.sub(
            r'jsonPath\("(?!(?:\\\$))\.([A-Za-z_][A-Za-z0-9_]*)"\)',
            r'jsonPath("$.\\1")',
            content,
        )

        # Repair the common malformed perform() pattern produced by Stage 3:
        # .content("") followed by .andExpect(...) without closing perform()).
        new_content = re.sub(
            r'(\.content\(""\)\s*)'
            r'(\.andExpect\()',
            r'\1)\n               \2',
            new_content,
        )

        # Small-model repair methods sometimes instantiate Jackson's
        # ObjectMapper without adding an import. Method-only repair cannot
        # change imports, so qualify the type inside the affected test method.
        new_content = re.sub(
            r"(?<![\w.])ObjectMapper\b",
            "com.fasterxml.jackson.databind.ObjectMapper",
            new_content,
        )

        if new_content != content:
            repaired[file_path] = new_content

    return repaired


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 4 - Run and repair generated tests"
    )
    parser.add_argument("--pr", required=True, help="GitHub pull request URL")
    args = parser.parse_args()

    settings = get_settings()
    github = GitHubClient(settings.github_token)
    generator = TestGenerator(model=settings.ollama_model)

    workspace_dir = settings.workspace_dir
    workspace_dir.mkdir(parents=True, exist_ok=True)

    pr = github.get_pr(args.pr)

    logging.info("PR #%s: %s", pr.number, pr.title)
    logging.info("Base branch: %s", pr.base_branch)
    logging.info("Head branch: %s", pr.head_branch)

    repo_dir = github.clone_and_checkout(pr, workspace_dir)
    logging.info("Repository: %s", repo_dir)

    generated_tests_file = workspace_dir / f"pr-{pr.number}-generated-tests.json"
    analysis_file = workspace_dir / f"pr-{pr.number}-analysis.json"
    result_file = workspace_dir / f"pr-{pr.number}-stage4-result.json"

    logging.info("Loading generated tests from: %s", generated_tests_file)
    generated_tests = find_generated_tests(generated_tests_file)
    logging.info("Found %d generated test file(s).", len(generated_tests))

    # Always start Stage 4 from the exact Stage 3 output.
    write_generated_tests(repo_dir, generated_tests)
    current_test_files = read_generated_test_files(repo_dir, generated_tests)

    analysis = load_analysis(analysis_file)
    source_files = find_changed_java_source_files(repo_dir, pr.changed_files)

    if not source_files:
        raise RuntimeError("No changed Java production source files were found.")

    logging.info("Production source files supplied to repair model:")
    for file_path in source_files:
        logging.info("  %s", file_path)

    result = {
        "pr": args.pr,
        "repository": f"{pr.owner}/{pr.repo}",
        "pr_number": pr.number,
        "repo_dir": str(repo_dir),
        "generated_test_files": list(current_test_files.keys()),
        "source_files": list(source_files.keys()),
        "max_repair_attempts": MAX_REPAIR_ATTEMPTS,
        "attempts": [],
        "success": False,
    }

    repair_attempts_used = 0
    maven_run = 0

    while True:
        maven_run += 1

        logging.info("")
        logging.info("==========================================")
        logging.info("MAVEN TEST RUN %d", maven_run)
        logging.info("==========================================")

        success, maven_output = run_maven_tests(repo_dir)

        attempt_result = {
            "maven_run": maven_run,
            "maven_passed": success,
        }

        if success:
            logging.info("Maven tests PASSED.")
            attempt_result["status"] = "passed"
            result["attempts"].append(attempt_result)
            result["success"] = True
            result["final_maven_run"] = maven_run

            result_file.write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )
            logging.info("Stage 4 result saved to: %s", result_file)
            return

        logging.error("Maven tests FAILED.")
        attempt_result["status"] = "failed"
        attempt_result["maven_output"] = maven_output[-20000:]
        result["attempts"].append(attempt_result)

        # First try safe deterministic repairs. These do not consume an LLM attempt.
        deterministic = apply_deterministic_repairs(current_test_files)

        if deterministic:
            candidate_tests = dict(current_test_files)
            candidate_tests.update(deterministic)

            try:
                validate_repaired_tests(current_test_files, candidate_tests)
                write_repaired_tests(repo_dir, deterministic)
                current_test_files = candidate_tests
                logging.info("Deterministic repair accepted.")
                attempt_result["repair"] = "deterministic_accepted"
                continue
            except Exception as exc:
                logging.exception("Deterministic repair rejected: %s", exc)

        if repair_attempts_used >= MAX_REPAIR_ATTEMPTS:
            logging.error("Maximum LLM repair attempts reached.")
            result["success"] = False
            result["final_maven_run"] = maven_run
            result_file.write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )
            logging.info("Stage 4 result saved to: %s", result_file)
            return

        repair_attempts_used += 1

        logging.info("")
        logging.info("==========================================")
        logging.info(
            "LLM METHOD REPAIR ATTEMPT %d/%d",
            repair_attempts_used,
            MAX_REPAIR_ATTEMPTS,
        )
        logging.info("==========================================")

        try:
            repaired_tests = generator.repair_tests(
                analysis=analysis,
                source_files=source_files,
                test_files=current_test_files,
                maven_output=maven_output,
            )

            # TestGenerator returns a list of {file_path, content} entries.
            # Normalize it to Stage 4's canonical dict[str, str] form.
            repaired_map = {}
            if isinstance(repaired_tests, dict):
                if "file_path" in repaired_tests and "content" in repaired_tests:
                    repaired_map[
                        str(repaired_tests["file_path"]).replace("\\", "/")
                    ] = str(repaired_tests["content"])
                else:
                    repaired_map = {
                        str(path).replace("\\", "/"): str(content)
                        for path, content in repaired_tests.items()
                    }
            elif isinstance(repaired_tests, list):
                for item in repaired_tests:
                    if not isinstance(item, dict):
                        raise ValueError("LLM repair entry must be an object.")
                    path = item.get("file_path") or item.get("path") or item.get("file")
                    content = item.get("content")
                    if not path or content is None:
                        raise ValueError(
                            "LLM repair entry must contain file_path and content."
                        )
                    repaired_map[str(path).replace("\\", "/")] = str(content)
            else:
                raise ValueError("LLM repair result must be a list or dict.")

            candidate_tests = dict(current_test_files)
            candidate_tests.update(repaired_map)

            validate_repaired_tests(current_test_files, candidate_tests)
            write_repaired_tests(repo_dir, repaired_map)
            current_test_files = candidate_tests

            logging.info("LLM repair accepted.")
            attempt_result["repair"] = "llm_accepted"

        except Exception as exc:
            logging.exception("LLM repair rejected: %s", exc)
            attempt_result["repair"] = "llm_rejected"
            attempt_result["repair_error"] = str(exc)

            # A rejected LLM response did not change the tests, so do not
            # waste another Maven run on identical files and identical
            # failure evidence. Retry the LLM while attempts remain.
            if repair_attempts_used >= MAX_REPAIR_ATTEMPTS:
                logging.error("Maximum LLM repair attempts reached.")
                result["success"] = False
                result["final_maven_run"] = maven_run
                result_file.write_text(
                    json.dumps(result, indent=2),
                    encoding="utf-8",
                )
                logging.info("Stage 4 result saved to: %s", result_file)
                return

            logging.info(
                "LLM repair rejected; retrying LLM without another Maven run."
            )
            continue


if __name__ == "__main__":
    main()
