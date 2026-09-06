from pathlib import Path

def build_context(repo_dir: Path, changed_files: list[dict], max_chars: int = 60000) -> str:
    candidates = []
    for item in changed_files:
        p = repo_dir / item["filename"]
        if p.exists() and p.suffix == ".java":
            candidates.append(p)

    for p in repo_dir.rglob("*.java"):
        rel = str(p.relative_to(repo_dir)).replace("\\", "/")
        if "/test/" in rel or rel.startswith("src/test/"):
            candidates.append(p)

    for name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        p = repo_dir / name
        if p.exists():
            candidates.append(p)

    unique, seen = [], set()
    for p in candidates:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)

    chunks, total = [], 0
    for p in unique:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        text = text[:remaining]
        chunks.append(f"===== {p.relative_to(repo_dir)} =====\n{text}")
        total += len(text)

    return "\n\n".join(chunks)
