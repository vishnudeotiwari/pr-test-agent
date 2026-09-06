import argparse
import fnmatch
import json
import logging
import shutil
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from git import Repo
from github import GithubException

from app.config import get_settings
from app.github_client import GitHubClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

STAGE5_RESULT_NAME = "pr-{pr_number}-stage5-result.json"


def load_stage4_result(workspace_dir: Path, pr_number: int) -> dict:
    result_path = workspace_dir / f"pr-{pr_number}-stage4-result.json"

    if not result_path.exists():
        raise FileNotFoundError(
            f"Stage 4 result not found: {result_path}. "
            "Run Stage 4 successfully before Stage 5."
        )

    with result_path.open("r", encoding="utf-8") as file:
        result = json.load(file)

    if not result.get("success"):
        raise RuntimeError(
            "Stage 4 did not finish successfully. "
            "Stage 5 will not create or push a test branch."
        )

    generated_files = result.get("generated_test_files") or []
    if not generated_files:
        raise RuntimeError("Stage 4 result contains no generated test files.")

    return result


def normalize_test_paths(stage4_result: dict) -> list[str]:
    paths = []

    for value in stage4_result.get("generated_test_files", []):
        if isinstance(value, str):
            path = value
        elif isinstance(value, dict):
            path = (
                value.get("file_path")
                or value.get("filename")
                or value.get("file")
                or value.get("path")
            )
            if not path:
                continue
        else:
            continue

        path = str(path).replace("\\", "/").lstrip("./")

        if path.startswith("src/test/") and path.endswith(".java"):
            paths.append(path)

    return list(dict.fromkeys(paths))


def backup_test_files(repo_dir: Path, test_paths: list[str], backup_dir: Path) -> None:
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    backup_dir.mkdir(parents=True, exist_ok=True)

    for relative_path in test_paths:
        source = repo_dir / Path(relative_path)

        if not source.exists():
            raise FileNotFoundError(
                f"Stage 4 test file does not exist in repository: {source}"
            )

        destination = backup_dir / Path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def restore_test_files(
    repo_dir: Path,
    test_paths: list[str],
    backup_dir: Path,
) -> None:
    for relative_path in test_paths:
        source = backup_dir / Path(relative_path)
        destination = repo_dir / Path(relative_path)

        if not source.exists():
            raise FileNotFoundError(
                f"Backed-up test file is missing: {source}"
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def fetch_remote_branches(repo: Repo) -> None:
    logging.info("Fetching latest remote branches.")
    repo.git.fetch("origin", "--prune")


def checkout_branch(repo: Repo, branch: str) -> None:
    logging.info("Checking out latest branch: %s", branch)
    repo.git.checkout("-B", branch, f"origin/{branch}")


def remote_branch_exists(repo: Repo, branch: str) -> bool:
    remote_branches = {
        ref.name.removeprefix("origin/")
        for ref in repo.remotes.origin.refs
    }
    return branch in remote_branches


def create_or_reset_test_branch(
    repo: Repo,
    source_branch: str,
    test_branch: str,
) -> None:
    logging.info(
        "Creating test branch '%s' from origin/%s.",
        test_branch,
        source_branch,
    )

    if remote_branch_exists(repo, test_branch):
        logging.info(
            "Remote branch '%s' already exists. "
            "It will be reset to origin/%s before publishing.",
            test_branch,
            source_branch,
        )

    repo.git.checkout("-B", test_branch, f"origin/{source_branch}")


def delete_existing_remote_test_branch(
    repo: Repo,
    test_branch: str,
    token: str,
) -> None:
    if not remote_branch_exists(repo, test_branch):
        return

    remote_url = repo.remotes.origin.url
    marker = "github.com/"

    if marker not in remote_url:
        raise RuntimeError(
            f"Unsupported GitHub remote URL for HTTPS push: {remote_url}"
        )

    repository_path = remote_url.split(marker, 1)[1]
    repository_path = repository_path.split("?", 1)[0].split("#", 1)[0]
    repository_path = repository_path.rstrip("/")

    if not repository_path.endswith(".git"):
        repository_path += ".git"

    encoded_token = quote(token, safe="")
    authenticated_push_url = (
        f"https://x-access-token:{encoded_token}@github.com/{repository_path}"
    )

    logging.info("Removing old remote test branch '%s'.", test_branch)

    try:
        repo.git.push(
            authenticated_push_url,
            f":refs/heads/{test_branch}",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to remove existing remote test branch '{test_branch}': {exc}"
        ) from exc


def is_ignorable_runtime_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")

    if normalized == "target" or normalized.startswith("target/"):
        return True

    runtime_patterns = (
        "data/*.mv.db",
        "data/*.trace.db",
    )

    return any(fnmatch.fnmatch(normalized, pattern) for pattern in runtime_patterns)


def remove_ignorable_runtime_artifacts(repo_dir: Path) -> None:
    candidates = [
        repo_dir / "target",
        repo_dir / "data",
    ]

    for candidate in candidates:
        if candidate.is_dir():
            if candidate.name == "target":
                logging.info("Removing Maven build directory: %s", candidate)
                shutil.rmtree(candidate, ignore_errors=True)

            elif candidate.name == "data":
                for path in candidate.glob("*.mv.db"):
                    logging.info(
                        "Removing runtime database artifact: %s",
                        path,
                    )
                    path.unlink(missing_ok=True)

                for path in candidate.glob("*.trace.db"):
                    logging.info(
                        "Removing runtime database artifact: %s",
                        path,
                    )
                    path.unlink(missing_ok=True)


def get_worktree_changes(repo: Repo) -> set[str]:
    changed = set()

    for item in repo.index.diff(None):
        changed.add(item.a_path.replace("\\", "/"))

    for item in repo.index.diff("HEAD"):
        changed.add(item.a_path.replace("\\", "/"))

    changed.update(
        path.replace("\\", "/")
        for path in repo.untracked_files
    )

    return changed


def verify_only_test_files_changed(
    repo: Repo,
    allowed_paths: set[str],
) -> None:
    changed = get_worktree_changes(repo)

    unexpected = sorted(
        path
        for path in changed
        if path not in allowed_paths
        and not is_ignorable_runtime_artifact(path)
    )

    if unexpected:
        raise RuntimeError(
            "Stage 5 detected changes outside generated test files: "
            + ", ".join(unexpected)
        )


def remove_ignorable_files_from_git_index(repo: Repo) -> None:
    staged_or_tracked_artifacts = [
        path
        for path in get_worktree_changes(repo)
        if is_ignorable_runtime_artifact(path)
    ]

    if not staged_or_tracked_artifacts:
        return

    logging.info(
        "Removing ignorable runtime artifacts from Git staging: %s",
        ", ".join(sorted(staged_or_tracked_artifacts)),
    )

    for path in staged_or_tracked_artifacts:
        try:
            repo.index.remove([path], working_tree=True)
        except Exception:
            pass


def commit_test_files(
    repo: Repo,
    test_paths: list[str],
    pr_number: int,
) -> str:
    remove_ignorable_files_from_git_index(repo)

    for relative_path in test_paths:
        repo.index.add([relative_path])

    verify_only_test_files_changed(repo, set(test_paths))

    if not repo.index.diff("HEAD") and not repo.index.diff(None):
        raise RuntimeError("No test-file changes are available to commit.")

    commit_message = f"test: add tests for PR #{pr_number}"
    commit = repo.index.commit(commit_message)

    logging.info("Created commit: %s", commit.hexsha)
    logging.info("Commit message: %s", commit_message)

    return commit.hexsha


def run_maven_tests(repo_dir: Path) -> None:
    logging.info("Running Maven tests on the final test branch.")

    # On Windows, Maven is normally exposed as mvn.cmd rather than a
    # directly executable "mvn" file. Stage 4 already works in this
    # environment, so use the Windows launcher explicitly.
    maven_command = "mvn.cmd" if os.name == "nt" else "mvn"

    result = subprocess.run(
        [maven_command, "test"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        logging.info("Maven stdout:\n%s", result.stdout)

    if result.stderr:
        logging.info("Maven stderr:\n%s", result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"Maven tests failed on the final Stage 5 branch "
            f"with exit code {result.returncode}."
        )

    logging.info("Maven tests passed on the final test branch.")


def push_branch(repo: Repo, test_branch: str, token: str) -> None:
    remote_url = repo.remotes.origin.url
    marker = "github.com/"

    if marker not in remote_url:
        raise RuntimeError(
            f"Unsupported GitHub remote URL for HTTPS push: {remote_url}"
        )

    repository_path = remote_url.split(marker, 1)[1]
    repository_path = repository_path.split("?", 1)[0].split("#", 1)[0]
    repository_path = repository_path.rstrip("/")

    if not repository_path.endswith(".git"):
        repository_path += ".git"

    encoded_token = quote(token, safe="")
    authenticated_push_url = (
        f"https://x-access-token:{encoded_token}@github.com/{repository_path}"
    )

    logging.info("Pushing branch '%s' to GitHub.", test_branch)

    repo.git.push(
        "--set-upstream",
        authenticated_push_url,
        test_branch,
        "--force",
    )


def create_test_pull_request(
    github: GitHubClient,
    owner: str,
    repo_name: str,
    original_pr_number: int,
    original_head_branch: str,
    test_branch: str,
) -> tuple[int, str, str]:
    repository = github.github.get_repo(f"{owner}/{repo_name}")

    title = (
        f"test: add automated tests for PR #{original_pr_number}"
    )

    body = (
        f"## Automated tests for PR #{original_pr_number}\n\n"
        f"This PR was generated by the PR Test Agent.\n\n"
        f"- Original PR: #{original_pr_number}\n"
        f"- Original PR head: `{original_head_branch}`\n"
        f"- Test branch: `{test_branch}`\n"
        f"- This test PR targets the original PR head branch.\n"
        f"- Tests generated and repaired by Stage 3/Stage 4\n"
        f"- Maven tests passed on the final test branch before PR creation\n\n"
        f"Generated at: {datetime.now(timezone.utc).isoformat()}"
    )

    logging.info(
        "Creating GitHub pull request: %s -> %s.",
        test_branch,
        original_head_branch,
    )

    pull_request = repository.create_pull(
        title=title,
        body=body,
        base=original_head_branch,
        head=test_branch,
    )

    return (
        pull_request.number,
        pull_request.html_url,
        pull_request.title,
    )


def save_stage5_result(
    workspace_dir: Path,
    pr_number: int,
    result: dict,
) -> Path:
    result_path = workspace_dir / STAGE5_RESULT_NAME.format(
        pr_number=pr_number
    )

    with result_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, indent=2)

    return result_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 5: create a test branch from the original PR head, "
            "run tests, push, and create a test PR."
        )
    )

    parser.add_argument(
        "--pr",
        required=True,
        help="GitHub pull request URL.",
    )

    parser.add_argument(
        "--branch",
        default=None,
        help="Optional test branch name. Defaults to pr-<number>-tests.",
    )

    args = parser.parse_args()

    settings = get_settings()
    github = GitHubClient(settings.github_token)

    pr = github.get_pr(args.pr)

    logging.info("PR #%s: %s", pr.number, pr.title)
    logging.info("Base branch: %s", pr.base_branch)
    logging.info("Head branch: %s", pr.head_branch)

    stage4_result = load_stage4_result(
        settings.workspace_dir,
        pr.number,
    )

    test_paths = normalize_test_paths(stage4_result)

    if not test_paths:
        raise RuntimeError(
            "No valid Java test files found in Stage 4 result."
        )

    repo_dir = Path(stage4_result["repo_dir"])

    if not repo_dir.exists():
        raise FileNotFoundError(
            f"Repository directory does not exist: {repo_dir}"
        )

    test_branch = args.branch or f"pr-{pr.number}-tests"

    logging.info("Stage 4 passed successfully.")
    logging.info("Test files to publish:")

    for path in test_paths:
        logging.info("  %s", path)

    repo = Repo(repo_dir)

    # Stage 4 leaves the repaired tests on the original PR branch.
    # Back them up before switching branches.
    backup_dir = (
        settings.workspace_dir
        / f"pr-{pr.number}-stage5-test-backup"
    )

    backup_test_files(
        repo_dir,
        test_paths,
        backup_dir,
    )

    try:
        fetch_remote_branches(repo)

        # The generated tests depend on the production code introduced
        # by the original PR. Therefore the test branch is based on
        # the original PR head, not the original PR base.
        checkout_branch(repo, pr.head_branch)

        # Reset any existing local test branch to the original PR head.
        create_or_reset_test_branch(
            repo,
            pr.head_branch,
            test_branch,
        )

        # If the remote test branch came from an earlier version of
        # Stage 5, remove it before publishing the corrected branch.
        if remote_branch_exists(repo, test_branch):
            delete_existing_remote_test_branch(
                repo,
                test_branch,
                settings.github_token,
            )

        remove_ignorable_runtime_artifacts(repo_dir)

        restore_test_files(
            repo_dir,
            test_paths,
            backup_dir,
        )

        remove_ignorable_runtime_artifacts(repo_dir)

        verify_only_test_files_changed(
            repo,
            set(test_paths),
        )

        # Verify the exact final branch before committing it.
        run_maven_tests(repo_dir)

        # Maven can create runtime artifacts. Remove them before committing.
        remove_ignorable_runtime_artifacts(repo_dir)
        verify_only_test_files_changed(
            repo,
            set(test_paths),
        )

        logging.info("Committing generated test files.")

        commit_sha = commit_test_files(
            repo,
            test_paths,
            pr.number,
        )

        push_branch(
            repo,
            test_branch,
            settings.github_token,
        )

        try:
            test_pr_number, test_pr_url, test_pr_title = (
                create_test_pull_request(
                    github=github,
                    owner=pr.owner,
                    repo_name=pr.repo,
                    original_pr_number=pr.number,
                    original_head_branch=pr.head_branch,
                    test_branch=test_branch,
                )
            )

        except GithubException as exc:
            raise RuntimeError(
                "The test branch was pushed, but GitHub rejected "
                "test PR creation. "
                f"GitHub error: {exc}"
            ) from exc

        result = {
            "success": True,
            "original_pr": args.pr,
            "original_pr_number": pr.number,
            "repository": f"{pr.owner}/{pr.repo}",
            "original_base_branch": pr.base_branch,
            "original_head_branch": pr.head_branch,
            "test_branch": test_branch,
            "test_pr_base_branch": pr.head_branch,
            "test_files": test_paths,
            "commit_sha": commit_sha,
            "test_pr_number": test_pr_number,
            "test_pr_url": test_pr_url,
            "test_pr_title": test_pr_title,
        }

        result_path = save_stage5_result(
            settings.workspace_dir,
            pr.number,
            result,
        )

        logging.info("")
        logging.info("==========================================")
        logging.info("STAGE 5 COMPLETE")
        logging.info("==========================================")
        logging.info("Test branch      : %s", test_branch)
        logging.info("Commit           : %s", commit_sha)
        logging.info("Test PR          : #%s", test_pr_number)
        logging.info("Test PR URL      : %s", test_pr_url)
        logging.info(
            "Test PR target   : %s",
            pr.head_branch,
        )
        logging.info("Result saved     : %s", result_path)

    finally:
        if backup_dir.exists():
            shutil.rmtree(
                backup_dir,
                ignore_errors=True,
            )


if __name__ == "__main__":
    main()
