import json
import requests


class LLMAnalyzer:

    def __init__(self, model="qwen2.5-coder:14b"):
        self.model = model
        self.url = "http://localhost:11434/api/chat"

    def analyze(self, diff: str, context: str) -> dict:

        prompt = f"""
You are a senior Java/Spring Boot test engineer.

Analyze the following GitHub Pull Request.

Your job is to understand the code changes and create a precise
test plan for the behavioral changes introduced by this PR.
Identify the behavioral changes that should be tested.

IMPORTANT:
- Base every statement strictly on the supplied PR DIFF and REPOSITORY CONTEXT.
- Do not invent IDs, HTTP status codes, response bodies, validation rules,
  database behavior, or business rules.
- Use exact values from the source code when they are relevant.
- If a behavior cannot be determined from the supplied context, explicitly
  say that it is unknown rather than guessing.
- Test cases must describe observable behavior of the actual implementation.

PR DIFF:
{diff}

REPOSITORY CONTEXT:
{context}

Instructions:

1. Identify the Java classes/components changed by the PR.
2. Identify the actual behavioral changes.
3. Identify which classes/methods should be tested.
4. Consider existing test patterns in the repository.
5. Prefer JUnit 5 and Mockito where appropriate.
6. Focus on behavior, not code coverage alone.
7. Include positive, negative, and edge-case scenarios where relevant.
8. Do NOT write test code yet.
9. Do NOT invent behavior that is not supported by the source code.
10. Return ONLY valid JSON.

Return JSON with exactly these top-level keys:

summary
changed_components
behavioral_changes
test_targets
test_cases
existing_test_patterns

Each test_cases item must contain exactly:

name
target
scenario
expected_behavior
priority

The priority must be one of:

HIGH
MEDIUM
LOW
"""

        try:
            response = requests.post(
                self.url,
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "stream": False,
                    "format": "json"
                },
                timeout=300
            )

            response.raise_for_status()

        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                "Could not connect to Ollama.\n"
                "Make sure Ollama is running and available at:\n"
                "http://localhost:11434"
            ) from exc

        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                "Ollama request timed out after 300 seconds."
            ) from exc

        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                f"Ollama request failed: {exc}"
            ) from exc

        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Ollama returned an invalid HTTP response:\n"
                f"{response.text}"
            ) from exc

        if "message" not in result:
            raise RuntimeError(
                f"Unexpected Ollama response:\n{result}"
            )

        content = result["message"].get("content")

        if not content:
            raise RuntimeError(
                f"Ollama returned an empty response:\n{result}"
            )

        try:
            analysis = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Ollama returned invalid JSON:\n"
                + content
            ) from exc

        return analysis