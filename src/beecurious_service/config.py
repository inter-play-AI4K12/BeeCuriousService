from dataclasses import dataclass
import os
from pathlib import Path


def load_dotenv(path: Path | None = None) -> Path | None:
    """Load unset environment variables from a dotenv file."""
    dotenv_path = path or Path.cwd() / ".env"
    if not dotenv_path.is_file():
        return None

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
    return dotenv_path


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the BeeCurious service."""
    host: str
    port: int
    provider: str
    model: str
    openai_api_key: str | None
    openai_base_url: str
    openai_org_id: str | None
    openai_project_id: str | None

    @classmethod
    def from_environment(cls) -> "Settings":
        """Build service settings from the current process environment."""
        return cls(
            host=os.getenv("BEECURIOUS_HOST", "127.0.0.1"),
            port=int(os.getenv("BEECURIOUS_PORT", "8765")),
            provider=os.getenv("BEECURIOUS_AGENT_PROVIDER", "mock").lower(),
            model=os.getenv("BEECURIOUS_MODEL", "gpt-5.2"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_org_id=os.getenv("OPENAI_ORG_ID"),
            openai_project_id=os.getenv("OPENAI_PROJECT_ID"),
        )
