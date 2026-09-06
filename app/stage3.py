import argparse
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.github_client import GitHubClient
from app.test_generator import TestGenerator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def find_changed_java_files(
    repo_dir: Path,
    changed_files,
) -> dict[str, str]:

    files = {}

    for changed_file in changed_files:

        # Support both dictionary and object representations.
        if isinstance(changed_file, dict):
            path_value = (
                changed_file.get("filename")
                or changed_file.get("file")
                or changed_file.get("path")
            )
        else:
            path_value = (
                getattr(changed_file, "filename", None)
                or getattr(changed_file, "file", None)
                or getattr(changed_file, "path", None)
            )

        if not path_value:
            logging.warning(
                "Could not determine changed file path: %r",
                changed_file,
            )
            continue

        path = Path(path_value)

        if path.suffix.lower() != ".java":
            continue

        full_path = repo_dir / path

        if not full_path.exists():
            logging.warning(
                "Changed Java file not found: %s",
                full_path,
            )
            continue

        files[
            str(path).replace("\\", "/")
        ] = full_path.read_text(
            encoding="utf-8"
        )

    return files

def find_relevant_existing_tests(
    repo_dir: Path,
    changed_java_files: dict[str, str],
) -> dict[str, str]:

    test_root = (
        repo_dir
        / "src"
        / "test"
        / "java"
    )

    if not test_root.exists():
        return {}

    files = {}

    # Extract class names from changed files.
    changed_class_names = set()

    for path, content in changed_java_files.items():

        class_name = Path(path).stem

        if class_name:
            changed_class_names.add(
                class_name
            )

    for java_file in test_root.rglob(
        "*.java"
    ):

        file_name = java_file.stem

        # Prefer tests whose names relate to
        # changed production classes.
        is_relevant = any(
            class_name.lower()
            in file_name.lower()
            for class_name
            in changed_class_names
        )

        if not is_relevant:
            continue

        relative = java_file.relative_to(
            repo_dir
        )

        files[
            str(relative).replace("\\", "/")
        ] = java_file.read_text(
            encoding="utf-8"
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

    client = GitHubClient(
        settings.github_token
    )

    logging.info("Reading PR...")

    pr = client.get_pr(args.pr)

    analysis_file = (
        settings.workspace_dir
        / f"pr-{pr.number}-analysis.json"
    )

    if not analysis_file.exists():

        raise RuntimeError(
            f"Stage 2 analysis file was not found:\n"
            f"{analysis_file}\n\n"
            f"Run Stage 2 first:\n\n"
            f"python -m app.stage2 "
            f"--pr {args.pr}"
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
        "Cloning/checking out PR branch..."
    )

    repo_dir = client.clone_and_checkout(
        pr,
        settings.workspace_dir,
    )

    logging.info(
        "Reading changed Java files..."
    )

    source_files = find_changed_java_files(
        repo_dir,
        pr.changed_files,
    )

    if not source_files:

        raise RuntimeError(
            "No changed Java source files were found."
        )

    logging.info(
        "Found %d changed Java file(s).",
        len(source_files),
    )

    for path in source_files:
        logging.info(
            "  Changed: %s",
            path,
        )

    logging.info(
        "Reading relevant existing tests..."
    )

    existing_tests = (
        find_relevant_existing_tests(
            repo_dir,
            source_files,
        )
    )

    logging.info(
        "Found %d relevant existing test file(s).",
        len(existing_tests),
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

    for test, path in zip(
        tests,
        written_files,
    ):

        print(f"\n  {path}")
        print(
            f"  Class  : "
            f"{test['class_name']}"
        )
        print(
            f"  Reason : "
            f"{test.get('reason', '')}"
        )

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