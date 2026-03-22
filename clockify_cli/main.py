import sys
from loguru import logger

from clockify_cli.config import load_config
from clockify_cli.constants import APP_VERSION, LOG_DIR, LOG_FILE


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        LOG_FILE,
        rotation="10 MB",
        retention=5,
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
        enqueue=True,
    )
    logger.debug(f"Clockify CLI {APP_VERSION} starting")


def app() -> None:
    """Entry point: parse args, set up logging, launch TUI."""
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"clockify-cli {APP_VERSION}")
        return

    _setup_logging()
    config = load_config()

    # Import here to avoid loading Textual until needed
    from clockify_cli.tui.app import ClockifyApp

    logger.debug(f"Config loaded: configured={config.is_configured()}")
    tui = ClockifyApp(config=config)
    tui.run()


if __name__ == "__main__":
    app()
