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

# Fibery API
FIBERY_BASE_URL = "https://{workspace}.fibery.io"
FIBERY_COMMANDS_PATH = "/api/commands"
FIBERY_LABOR_COSTS_TYPE = "Agreement Management/Labor Costs"
FIBERY_CLOCKIFY_USERS_TYPE = "Agreement Management/Clockify Users"
FIBERY_AGREEMENTS_TYPE = "Agreement Management/Agreements"
FIBERY_MAX_CONCURRENT = 3   # Fibery rate limit: 3 req/sec
FIBERY_BATCH_SIZE = 50      # entities per batch/create-or-update call

# App metadata
APP_NAME = "Clockify CLI"
APP_VERSION = "0.2.0"
