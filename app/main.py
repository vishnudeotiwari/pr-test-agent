import argparse
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.github_client import GitHubClient
from app.context_builder import build_context
from app.test_generator import TestGenerator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

def find_changed_java_files(repo_dir: Path, changed_files) -> dict[str, str]:
    """
    Read only Java files changed by the PR.
    """

    files = {}

    for changed_file in changed_files:

        path = Path(changed_file.path)

        if path.suffix != ".java":
            continue

        full_path = repo_dir / path

        if not full_path.exists():
            continue

        files[str(path).replace("\\", "/")] = (
            full_path.read_text(encoding="utf-8")
        )

    return files

def find_java_source_files(repo_dir: Path) -> dict[str, str]:
    """
    Read Java production files from src/main/java.
    """

    source_root = repo_dir / "src" / "main" / "java"

    if not source_root.exists():
        return {}

    files = {}

    for java_file in source_root.rglob("*.java"):
        relative = java_file.relative_to(repo_dir)

        files[str(relative).replace("\\", "/")] = (
            java_file.read_text(encoding="utf-8")
        )

    return files


def find_existing_test_files(repo_dir: Path) -> dict[str, str]:
    """
    Read existing Java test files from src/test/java.
    """

    test_root = repo_dir / "src" / "test" / "java"

    if not test_root.exists():
        return {}

    files = {}

    for java_file in test_root.rglob("*.java"):
        relative = java_file.relative_to(repo_dir)

        files[str(relative).replace("\\", "/")] = (
            java_file.read_text(encoding="utf-8")
        )

    return files


def main():

    parser = argparse.ArgumentParser(
        description="PR Test Agent - Stage 3"
    )

    parser.add_argument(
        "--pr",
        required=True,
        help="GitHub pull request URL",
    )

    args = parser.parse_args()

    settings = get_settings()

    client = GitHubClient(settings.github_token)

    logging.info("Reading PR...")

    pr = client.get_pr(args.pr)

    logging.info("Fetching complete diff...")

    diff = client.get_diff(args.pr)

    logging.info("Cloning/checking out PR branch...")

    repo_dir = client.clone_and_checkout(
        pr,
        settings.workspace_dir,
    )

    analysis_file = (
        settings.workspace_dir
        / f"pr-{pr.number}-analysis.json"
    )

    if not analysis_file.exists():
        raise RuntimeError(
            f"Stage 2 analysis file was not found:\n"
            f"{analysis_file}\n\n"
            "Run Stage 2 first."
        )

    logging.info(
        "Loading Stage 2 analysis..."
    )

    analysis = json.loads(
        analysis_file.read_text(
            encoding="utf-8"
        )
    )

    logging.info(
        "Reading production Java source files..."
    )

    source_files = find_changed_java_files(
    repo_dir,
    pr.changed_files
  )

    logging.info(
        "Reading existing Java tests..."
    )

    existing_tests = find_existing_test_files(
        repo_dir
    )

    logging.info(
        "Generating tests with Ollama..."
    )

    generator = TestGenerator(
        settings.ollama_model
    )

    tests = generator.generate(
        analysis=analysis,
        source_files=source_files,
        existing_tests=existing_tests,
    )

    logging.info(
        "Generated %d test file(s).",
        len(tests),
    )

    logging.info(
        "Writing generated tests..."
    )

    written_files = generator.write_tests(
        repo_dir,
        tests,
    )

    # Save a record of what Stage 3 generated.
    generated_file = (
        settings.workspace_dir
        / f"pr-{pr.number}-generated-tests.json"
    )

    generated_file.write_text(
        json.dumps(
            tests,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print(f"PR #{pr.number}: {pr.title}")
    print("=" * 70)

    print("\nGenerated test files:")

    for test, path in zip(tests, written_files):
        print(f"\n  {path}")
        print(f"  Class  : {test['class_name']}")
        print(f"  Reason : {test.get('reason', '')}")

    print(
        f"\nGeneration metadata saved to: "
        f"{generated_file}"
    )

    print(
        "\nIMPORTANT: Stage 3 has NOT run Maven tests."
    )

    print(
        "IMPORTANT: Stage 3 has NOT committed or pushed anything."
    )

    print(
        "\nStage 3 completed successfully."
    )


if __name__ == "__main__":
    main()