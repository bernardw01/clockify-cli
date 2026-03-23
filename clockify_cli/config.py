import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

from clockify_cli.constants import CONFIG_DIR, CONFIG_FILE


@dataclass
class Config:
    api_key: str = ""
    workspace_id: str = ""
    workspace_name: str = ""
    last_sync: Optional[str] = None  # ISO datetime of last full sync
    fibery_api_key: str = ""
    fibery_workspace: str = "harpin-ai"

    def get_api_key(self) -> str:
        """Return env var override if set, else stored key."""
        return os.environ.get("CLOCKIFY_API_KEY", self.api_key)

    def get_fibery_api_key(self) -> str:
        """Return Fibery env var override if set, else stored key."""
        return os.environ.get("FIBERY_API_KEY", self.fibery_api_key)

    def is_configured(self) -> bool:
        """True if a Clockify API key is available and a workspace is selected."""
        return bool(self.get_api_key()) and bool(self.workspace_id)

    def is_fibery_configured(self) -> bool:
        """True if a Fibery API key is set."""
        return bool(self.get_fibery_api_key())


def load_config() -> Config:
    """Load config from disk; return defaults if file does not exist."""
    if not CONFIG_FILE.exists():
        return Config()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return Config()


def save_config(config: Config) -> None:
    """Persist config to disk with restricted permissions (600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    CONFIG_FILE.chmod(0o600)
