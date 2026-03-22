from pathlib import Path

# Clockify API
BASE_URL = "https://api.clockify.me/api/v1"
DEFAULT_PAGE_SIZE = 50
MAX_REQUESTS_PER_SECOND = 10  # conservative; API allows 50 with addon token

# Local paths
CONFIG_DIR = Path.home() / ".config" / "clockify-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / "clockify-cli"
DB_PATH = DATA_DIR / "clockify.db"
LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "clockify-cli.log"

# App metadata
APP_NAME = "Clockify CLI"
APP_VERSION = "0.1.0"
