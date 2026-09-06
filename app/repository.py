from pathlib import Path
import subprocess

def run_git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git command failed: git {' '.join(args)}\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()

def list_java_files(repo_dir: Path) -> list[str]:
    return sorted(
        str(p.relative_to(repo_dir))
        for p in repo_dir.rglob("*.java")
        if ".git" not in p.parts
    )

def read_file(repo_dir: Path, relative_path: str) -> str:
    path = (repo_dir / relative_path).resolve()
    if repo_dir.resolve() not in path.parents and path != repo_dir.resolve():
        raise ValueError("Path escapes repository directory.")
    return path.read_text(encoding="utf-8")
