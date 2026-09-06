from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import requests
from github import Github
from git import Repo

@dataclass
class PullRequestInfo:
    owner: str
    repo: str
    number: int
    title: str
    base_branch: str
    head_branch: str
    clone_url: str
    ssh_url: str
    changed_files: list[dict]

class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self.github = Github(token)

    @staticmethod
    def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
        parsed = urlparse(pr_url.strip())
        if parsed.netloc.lower() != "github.com":
            raise ValueError("Only github.com PR URLs are supported.")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 4 or parts[2].lower() != "pull":
            raise ValueError("Expected URL format: https://github.com/OWNER/REPO/pull/NUMBER")
        try:
            return parts[0], parts[1], int(parts[3])
        except ValueError as exc:
            raise ValueError("PR number must be an integer.") from exc

    def get_pr(self, pr_url: str) -> PullRequestInfo:
        owner, repo_name, number = self.parse_pr_url(pr_url)
        repo = self.github.get_repo(f"{owner}/{repo_name}")
        pr = repo.get_pull(number)
        changed_files = []
        for file in pr.get_files():
            changed_files.append({
                "filename": file.filename,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "changes": file.changes,
                "patch": file.patch or "",
            })
        return PullRequestInfo(
            owner=owner, repo=repo_name, number=number, title=pr.title,
            base_branch=pr.base.ref, head_branch=pr.head.ref,
            clone_url=repo.clone_url, ssh_url=repo.ssh_url,
            changed_files=changed_files,
        )

    def get_diff(self, pr_url: str) -> str:
        owner, repo_name, number = self.parse_pr_url(pr_url)
        diff_url = f"https://github.com/{owner}/{repo_name}/pull/{number}.diff"
        response = requests.get(
            diff_url,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github.diff"},
            timeout=30,
        )
        response.raise_for_status()
        return response.text

    def clone_and_checkout(self, pr: PullRequestInfo, workspace_dir: Path) -> Path:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        target = workspace_dir / f"{pr.repo}-pr-{pr.number}"
        if target.exists():
            repo = Repo(target)
            repo.git.fetch("--all", "--prune")
        else:
            authenticated_url = pr.clone_url.replace(
                "https://github.com/",
                f"https://x-access-token:{self.token}@github.com/",
                1,
            )
            Repo.clone_from(authenticated_url, target)
        repo = Repo(target)
        repo.git.fetch("origin", pr.head_branch)
        repo.git.checkout("-B", pr.head_branch, f"origin/{pr.head_branch}")
        return target
