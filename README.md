# PR Test Agent

An AI-powered developer agent that analyzes a GitHub Pull Request, understands the code changes, generates JUnit 5 / Mockito tests using a local Ollama model, executes the tests with Maven, repairs failures, and publishes the generated tests as a separate GitHub Pull Request.

## Overview

The PR Test Agent automates the following workflow:

```text
                         GitHub
                           │
                           ▼
                    Original Pull Request
                           │
                           ▼
                    ┌─────────────┐
                    │   Stage 1   │
                    │ Read PR and │
                    │ clone repo  │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Stage 2   │
                    │ Understand  │
                    │ PR + create │
                    │ test plan   │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Stage 3   │
                    │ Generate    │
                    │ JUnit/      │
                    │ Mockito     │
                    │ tests       │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Stage 4   │
                    │ Run Maven   │
                    │ tests +     │
                    │ repair      │
                    │ failures    │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Stage 5   │
                    │ Commit +    │
                    │ push +      │
                    │ create PR   │
                    └──────┬──────┘
                           │
                           ▼
                    Generated Test Pull Request
```

The generated test PR is intentionally created **against the original PR's head branch**.

For example:

```text
main
  ↑
testpr                  ← original PR branch
  ↑
pr-1-tests              ← generated test branch
```

Therefore:

```text
PR #1: testpr → main

PR #2: pr-1-tests → testpr
```

This allows the generated tests to compile against the code introduced by the original PR.

---

# Features

- Read GitHub Pull Requests
- Retrieve changed files and diffs
- Clone and checkout the PR branch
- Analyze Java source code
- Generate test plans using a local LLM
- Generate JUnit 5 / Mockito tests
- Run Maven tests automatically
- Repair generated tests after compilation/test failures
- Limit automated repair attempts
- Ignore runtime artifacts such as H2 database files
- Create a dedicated test branch
- Commit generated tests
- Push the branch to GitHub
- Create a separate GitHub Pull Request
- Save stage results as JSON artifacts

---

# Technology Stack

## Agent

- Python 3.12
- PyGithub
- GitPython
- Requests
- Pydantic
- python-dotenv

## AI

- Ollama
- `qwen2.5-coder:14b`

## Target applications

The current version is optimized for:

- Java
- Spring Boot
- Maven
- JUnit 5
- Mockito

---

# Project Structure

A typical project structure is:

```text
pr-test-agent/
│
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── github_client.py
│   ├── stage1.py
│   ├── stage2.py
│   ├── stage3.py
│   ├── stage4.py
│   ├── stage5.py
│   └── test_generator.py
│
├── workspace/
│
├── .env
├── requirements.txt
└── README.md
```

The `workspace` directory is generated/used by the agent for cloned repositories, diffs, generated tests, and stage results.

---

# Prerequisites

Install the following before running the agent.

## Python

Python 3.12 is recommended.

Verify:

```powershell
python --version
```

or:

```powershell
py --version
```

Expected:

```text
Python 3.12.x
```

---

## Git

Verify:

```powershell
git --version
```

---

## Java

Verify:

```powershell
java -version
```

The current target Spring Boot project uses Java 21.

A newer JDK may also work depending on the target project's Maven configuration.

---

## Maven

Verify:

```powershell
mvn -version
```

On Windows also verify:

```powershell
where.exe mvn
```

The agent uses `mvn.cmd` on Windows when executing Maven tests.

---

# Ollama Setup

Install Ollama and verify:

```powershell
ollama --version
```

Install the coding model:

```powershell
ollama pull qwen2.5-coder:14b
```

Verify:

```powershell
ollama list
```

You should see:

```text
qwen2.5-coder:14b
```

Check the Ollama server:

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
```

You can also check loaded models:

```powershell
ollama ps
```

The agent expects Ollama to be available at:

```text
http://localhost:11434
```

---

# Ollama Logs

On Windows, Ollama logs can be inspected with:

```powershell
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 100
```

To continuously watch the log:

```powershell
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Wait
```

This is useful when Stage 2 or Stage 3 appears to be taking a long time.

---

# Python Environment Setup

Navigate to the project:

```powershell
cd "C:\Users\vishn\Downloads\pr-test-agent-stage1\pr-test-agent"
```

Create a virtual environment:

```powershell
py -3.12 -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then activate again:

```powershell
.\.venv\Scripts\Activate.ps1
```

Upgrade pip:

```powershell
python -m pip install --upgrade pip
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

---

# Configuration

Create a `.env` file in the project root.

Example:

```text
GITHUB_TOKEN=your_github_personal_access_token
OLLAMA_MODEL=qwen2.5-coder:14b
WORKSPACE_DIR=./workspace
OLLAMA_TIMEOUT=600
```

## GitHub Token

The GitHub Personal Access Token must have sufficient access to:

- Read the repository
- Read pull requests
- Push branches
- Create pull requests

Never commit `.env` to Git.

Add it to `.gitignore`:

```text
.env
.venv/
workspace/
__pycache__/
*.pyc
```

---

# Running the Agent

The pipeline currently consists of five stages.

Each stage is executed separately.

This makes it easier to inspect intermediate results and troubleshoot failures.

---

# Stage 1 — GitHub PR Integration

Stage 1:

- Reads the GitHub PR
- Gets PR metadata
- Gets changed files
- Gets the PR diff
- Clones the repository
- Checks out the PR head branch

Run:

```powershell
python -m app.stage1 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Example:

```powershell
python -m app.stage1 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Expected output resembles:

```text
PR #1: pr testing
Repository : vishnudeotiwari/spring-boot-project
Base       : main
Head       : testpr
Changed:
  ...
Java files: ...
Diff saved: workspace/pr-1.diff
```

---

# Stage 2 — PR Understanding and Test Plan

Stage 2 sends the relevant source/change information to Ollama.

The model identifies:

- Changed classes
- Important methods
- Expected behavior
- Success cases
- Failure cases
- Edge cases
- Suggested test scenarios

Run:

```powershell
python -m app.stage2 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Example:

```powershell
python -m app.stage2 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

The resulting test plan is saved in the workspace.

---

# Stage 3 — Test Generation

Stage 3 uses the Stage 2 test plan and source code to generate Java tests.

The current target is:

- JUnit 5
- Mockito
- Spring Boot controller/service testing

Run:

```powershell
python -m app.stage3 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Example:

```powershell
python -m app.stage3 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Generated tests are written into the cloned repository workspace.

A generated test may look like:

```text
src/test/java/com/example/blogging/controller/EmployeeControllerTest.java
```

Stage 3 does **not** commit or push anything.

---

# Stage 4 — Maven Test Execution and Repair

Stage 4 validates the generated tests.

The workflow is:

```text
Generated tests
      │
      ▼
Run Maven
      │
      ├── PASS ──────────────► Success
      │
      ▼
Compilation/test failure
      │
      ▼
Attempt deterministic repair
      │
      ▼
Attempt LLM repair if required
      │
      ▼
Run Maven again
```

Run:

```powershell
python -m app.stage4 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Example:

```powershell
python -m app.stage4 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

A successful result contains:

```json
{
  "success": true
}
```

The stage result is saved as:

```text
workspace/pr-1-stage4-result.json
```

The current implementation limits automated repair attempts to approximately three repair cycles.

---

# Stage 5 — Publish Test PR

Stage 5 publishes the generated tests.

It:

1. Reads the Stage 4 result
2. Checks out the original PR head branch
3. Creates the generated test branch
4. Restores generated test files
5. Runs final Maven verification
6. Removes known runtime artifacts
7. Verifies the intended test changes
8. Creates a Git commit
9. Pushes the branch
10. Creates a GitHub Pull Request

Run:

```powershell
python -m app.stage5 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Example:

```powershell
python -m app.stage5 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

The generated branch follows the pattern:

```text
pr-<number>-tests
```

For PR #1:

```text
pr-1-tests
```

The generated test PR targets the original PR's head branch.

For example:

```text
pr-1-tests → testpr
```

---

# Complete Execution

Once everything is installed, the complete workflow is:

```powershell
cd "C:\Users\vishn\Downloads\pr-test-agent-stage1\pr-test-agent"

.\.venv\Scripts\Activate.ps1

python -m app.stage1 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage2 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage3 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage4 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage5 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

---

# Example End-to-End Flow

Suppose the original PR is:

```text
https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

Original PR:

```text
PR #1

testpr → main
```

The agent analyzes the changes and generates:

```text
EmployeeControllerTest.java
```

Stage 4 validates the test.

Stage 5 creates:

```text
Branch:
pr-1-tests
```

and finally:

```text
PR #2

pr-1-tests → testpr
```

The final Git history is conceptually:

```text
main
 │
 └── testpr
      │
      └── pr-1-tests
```

This is intentional because the generated tests depend on the code introduced by the original PR.

---

# Workspace Artifacts

The `workspace` directory contains intermediate and final artifacts.

Typical files include:

```text
workspace/
│
├── pr-1.diff
├── pr-1-generated-tests.json
├── pr-1-stage4-result.json
├── pr-1-stage5-result.json
│
└── pr-1/
    └── cloned target repository
```

## Stage 1

```text
pr-1.diff
```

Contains the Pull Request diff.

## Stage 3

```text
pr-1-generated-tests.json
```

Contains information about generated tests.

## Stage 4

```text
pr-1-stage4-result.json
```

Contains Maven execution and repair information.

## Stage 5

```text
pr-1-stage5-result.json
```

Contains publishing information including the generated branch, commit and Pull Request.

---

# Runtime Artifacts

Some Spring Boot applications create runtime files during tests.

For example:

```text
data/blogdb.mv.db
data/blogdb.trace.db
```

These are not intended to be part of the generated test PR.

Stage 5 recognizes known runtime artifacts and removes them before committing.

Likewise, Maven's:

```text
target/
```

directory is treated as a build artifact rather than a source change.

---

# Current V1 Scope

The current version intentionally has a limited scope.

## Supported

- GitHub Pull Requests
- Java source
- Maven projects
- Spring Boot
- JUnit 5
- Mockito
- Local Ollama models
- Generated test files
- Separate test Pull Requests

## Current limitation

The V1 agent is primarily designed to modify **test files** rather than production source code.

The generated test branch should not modify application implementation code.

---

# Design Principles

## 1. Local AI

The LLM runs locally through Ollama.

This means the generated source analysis does not need to be sent to a hosted LLM API.

---

## 2. Separate Test PR

The agent does not directly modify the original Pull Request.

Instead:

```text
Original PR
     │
     ▼
Analyze
     │
     ▼
Generate tests
     │
     ▼
Separate test branch
     │
     ▼
Separate test PR
```

This provides a clean review boundary.

---

## 3. Tests Must Validate the PR

Generated tests are based on the behavior introduced by the target Pull Request.

The agent should avoid inventing behavior that is not supported by the source code.

---

## 4. Automated Repair

Generated tests may initially contain:

- Syntax errors
- Incorrect imports
- Incorrect API usage
- Incorrect assumptions about the source

Stage 4 attempts to repair these problems automatically and reruns Maven.

---

# Troubleshooting

## Ollama timeout

If Stage 2 or Stage 3 reports a timeout such as:

```text
ReadTimeout
```

increase:

```text
OLLAMA_TIMEOUT=600
```

in `.env`.

Check:

```powershell
ollama ps
```

and inspect logs:

```powershell
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 100
```

---

## Maven not found

Check:

```powershell
where.exe mvn
```

and:

```powershell
mvn -version
```

On Windows the agent uses:

```text
mvn.cmd
```

for Maven execution.

If Maven works in your terminal but not inside Python, verify that Maven's `bin` directory is present in `PATH`.

---

## GitHub 403 error

If branch pushing works but Pull Request creation returns:

```text
403 Resource not accessible by personal access token
```

check the GitHub Personal Access Token permissions.

The token needs permission to create Pull Requests and access the target repository.

After changing token permissions, update the value in `.env`.

---

## Existing test branch

If:

```text
pr-1-tests
```

already exists, Stage 5 may reset/recreate the branch as part of its publishing workflow.

If a previous run left the repository in an unexpected state, inspect:

```powershell
git status
git branch
git remote -v
```

---

## Maven creates database files

If you see:

```text
data/blogdb.mv.db
```

or similar H2 runtime artifacts, these are expected runtime files and are removed/ignored by Stage 5.

Do not add them to the generated test PR.

---

# Recommended Debugging Sequence

When a stage fails, identify the stage first.

```text
Stage 1 → GitHub/repository problem

Stage 2 → LLM/test-plan problem

Stage 3 → Test-generation/LLM problem

Stage 4 → Java/Maven/generated-test problem

Stage 5 → Git/branch/GitHub PR problem
```

Run the stages individually rather than rerunning the entire pipeline immediately.

---

# Example Successful Run

A successful Stage 5 run looks like:

```text
Created commit: 794d0a51fdfe2443e99b6b466ba65eeed1c5a87d

Pushing branch 'pr-1-tests' to GitHub.

Creating GitHub pull request: pr-1-tests -> testpr.

==========================================
STAGE 5 COMPLETE
==========================================

Test branch      : pr-1-tests
Commit           : 794d0a51fdfe2443e99b6b466ba65eeed1c5a87d
Test PR          : #2
Test PR URL      : https://github.com/OWNER/REPOSITORY/pull/NUMBER
Test PR target   : testpr
```

At this point the generated tests are available for review in GitHub.

---

# Future Improvements

Potential future versions can extend the current V1 architecture with:

- Multiple changed Java classes
- Service-layer test generation
- Repository-layer test generation
- Existing-test detection
- Test deduplication
- Code coverage analysis
- JaCoCo integration
- Coverage-driven test generation
- Better failure classification
- More sophisticated repair strategies
- Automatic test PR updates
- CI status monitoring
- Support for more build systems
- Support for additional LLM providers
- Automatic handling of existing test branches
- Better test quality scoring
- Full pipeline command instead of manually running five stages

A future unified command could eventually become:

```powershell
python -m app.run --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```

which would execute all stages automatically.

---

# Security Notes

Never commit secrets to the repository.

Do not commit:

```text
.env
GitHub tokens
API keys
Ollama credentials
```

Recommended `.gitignore` entries:

```text
.env
.venv/
workspace/
__pycache__/
*.pyc
```

If a GitHub token is accidentally exposed, revoke it immediately and create a new token.

---

# License

Add the project's intended license here before publishing the repository publicly.

---

# Status

Current implementation:

```text
Stage 1  ✅
Stage 2  ✅
Stage 3  ✅
Stage 4  ✅
Stage 5  ✅
```

The current V1 pipeline has been successfully demonstrated end-to-end with a generated test Pull Request.

---

# Quick Reference

```powershell
# Activate environment
.\.venv\Scripts\Activate.ps1

# Stage 1
python -m app.stage1 --pr <PR_URL>

# Stage 2
python -m app.stage2 --pr <PR_URL>

# Stage 3
python -m app.stage3 --pr <PR_URL>

# Stage 4
python -m app.stage4 --pr <PR_URL>

# Stage 5
python -m app.stage5 --pr <PR_URL>
```

For the current test repository:

```powershell
python -m app.stage1 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage2 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage3 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage4 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER

python -m app.stage5 --pr https://github.com/OWNER/REPOSITORY/pull/NUMBER
```