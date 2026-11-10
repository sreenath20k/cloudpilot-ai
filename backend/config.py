"""
Runtime settings, read from environment variables (never hard-coded credentials).

AWS credentials are NOT configured here: boto3 finds them automatically
(~/.aws/credentials from `aws configure`, environment variables, or an IAM role).
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Optional: read settings (including OPENAI_API_KEY) from backend/.env for local runs.
# .env is git-ignored and docker-ignored, so the key never reaches GitHub or the image.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:  # python-dotenv is optional
    pass


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    aws_region: str
    ecs_cluster: str
    ecs_service: str
    # The ECS management API (/service/*, /chat) is OFF by default, so the copy of
    # this app running publicly on ECS only serves / and /health. It is switched on
    # for the local chat backend with CONTROL_API_ENABLED=true.
    control_api_enabled: bool
    # Safety limits for changes requested through chat (keeps cost predictable).
    min_desired_count: int
    max_desired_count: int
    max_cpu: int
    max_memory: int
    # Natural-language interpreter: "auto" = OpenAI when a key is set, otherwise rules.
    parser_mode: str
    openai_api_key: str | None
    openai_model: str


def load_settings() -> Settings:
    return Settings(
        aws_region=os.getenv("AWS_REGION", "ap-southeast-2"),
        ecs_cluster=os.getenv("ECS_CLUSTER", "cloudpilot-ai-cluster"),
        ecs_service=os.getenv("ECS_SERVICE", "cloudpilot-ai-service"),
        control_api_enabled=_env_bool("CONTROL_API_ENABLED", False),
        min_desired_count=int(os.getenv("MIN_DESIRED_COUNT", "0")),
        max_desired_count=int(os.getenv("MAX_DESIRED_COUNT", "5")),
        max_cpu=int(os.getenv("MAX_CPU", "1024")),
        max_memory=int(os.getenv("MAX_MEMORY", "4096")),
        parser_mode=os.getenv("PARSER_MODE", "auto").strip().lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
    )
