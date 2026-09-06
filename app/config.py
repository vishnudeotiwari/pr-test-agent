from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()


class Settings(BaseModel):
    github_token: str
    ollama_model: str = "qwen2.5-coder:14b"
    workspace_dir: Path = Path("./workspace")


def get_settings() -> Settings:

    github_token = os.getenv("GITHUB_TOKEN")

    if not github_token:
        raise RuntimeError("GITHUB_TOKEN is not set.")

    return Settings(
        github_token=github_token,
        ollama_model=os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5-coder:14b"
        ),
        workspace_dir=Path(
            os.getenv("WORKSPACE_DIR", "./workspace")
        ).resolve(),
    )