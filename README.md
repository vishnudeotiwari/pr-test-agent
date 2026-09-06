# PR Test Agent — Stage 2

Stage 2 analyzes a GitHub Java/Spring Boot PR and creates a structured AI test plan.

## Setup

Copy `.env.example` to `.env` and set your GitHub and OpenAI keys.

```powershell
pip install -r requirements.txt
```

Run:

```powershell
python -m app.main --pr https://github.com/OWNER/REPOSITORY/pull/1
```

The analysis is saved to `workspace/pr-1-analysis.json`.

## Safety boundary

Stage 2 only reads the PR/repository and produces an analysis. It does not
modify source/test files, push branches, or create GitHub PRs.
