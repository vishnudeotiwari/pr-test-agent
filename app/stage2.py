import argparse
import json
import logging

from app.config import get_settings
from app.github_client import GitHubClient
from app.context_builder import build_context
from app.llm_analyzer import LLMAnalyzer


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def main():

    parser = argparse.ArgumentParser(
        description="PR Test Agent - Stage 2"
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

    logging.info("Fetching complete diff...")

    diff = client.get_diff(args.pr)

    logging.info(
        "Cloning/checking out PR branch..."
    )

    repo_dir = client.clone_and_checkout(
        pr,
        settings.workspace_dir,
    )

    logging.info(
        "Building repository context..."
    )

    context = build_context(
        repo_dir,
        pr.changed_files,
    )

    logging.info(
        "Analyzing PR with Ollama..."
    )

    analyzer = LLMAnalyzer(
        settings.ollama_model
    )

    analysis = analyzer.analyze(
        diff,
        context,
    )

    output_file = (
        settings.workspace_dir
        / f"pr-{pr.number}-analysis.json"
    )

    output_file.write_text(
        json.dumps(
            analysis,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print(f"PR #{pr.number}: {pr.title}")
    print("=" * 70)

    print("\nSummary:")
    print(analysis.get("summary", ""))

    print("\nBehavioral changes:")

    behavioral_changes = analysis.get(
        "behavioral_changes",
        [],
    )

    if isinstance(
        behavioral_changes,
        dict,
    ):
        for key, value in behavioral_changes.items():
            print(f"  - {key}: {value}")
    else:
        for item in behavioral_changes:
            print(f"  - {item}")

    print("\nTest cases:")

    for i, test in enumerate(
        analysis.get("test_cases", []),
        1,
    ):
        print(f"  {i}. {test.get('name')}")
        print(f"     Target   : {test.get('target')}")
        print(f"     Scenario : {test.get('scenario')}")
        print(
            f"     Expected : "
            f"{test.get('expected_behavior')}"
        )
        print(
            f"     Priority : "
            f"{test.get('priority')}"
        )

    print(
        f"\nAnalysis saved to: "
        f"{output_file}"
    )

    print(
        "\nStage 2 completed successfully."
    )


if __name__ == "__main__":
    main()