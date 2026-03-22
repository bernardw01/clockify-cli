"""Tests for config load/save and Config methods."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from clockify_cli.config import Config, load_config, save_config


def test_config_defaults():
    cfg = Config()
    assert cfg.api_key == ""
    assert cfg.workspace_id == ""
    assert cfg.is_configured() is False


def test_config_is_configured_needs_both():
    assert Config(api_key="key123").is_configured() is False
    assert Config(workspace_id="ws1").is_configured() is False
    assert Config(api_key="key123", workspace_id="ws1").is_configured() is True


def test_get_api_key_env_override():
    cfg = Config(api_key="stored_key")
    with patch.dict(os.environ, {"CLOCKIFY_API_KEY": "env_key"}):
        assert cfg.get_api_key() == "env_key"
    assert cfg.get_api_key() == "stored_key"


def test_get_api_key_fallback():
    cfg = Config(api_key="stored_key")
    env = {k: v for k, v in os.environ.items() if k != "CLOCKIFY_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        assert cfg.get_api_key() == "stored_key"


def test_save_and_load_config(tmp_path: Path):
    cfg = Config(api_key="abc", workspace_id="ws-1", workspace_name="My Co")
    config_file = tmp_path / "config.json"

    with patch("clockify_cli.config.CONFIG_DIR", tmp_path), \
         patch("clockify_cli.config.CONFIG_FILE", config_file):
        save_config(cfg)
        assert config_file.exists()
        assert oct(config_file.stat().st_mode)[-3:] == "600"

        loaded = load_config()

    assert loaded.api_key == "abc"
    assert loaded.workspace_id == "ws-1"
    assert loaded.workspace_name == "My Co"


def test_load_config_missing_file(tmp_path: Path):
    config_file = tmp_path / "missing.json"
    with patch("clockify_cli.config.CONFIG_FILE", config_file):
        cfg = load_config()
    assert cfg.api_key == ""


def test_load_config_corrupt_file(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text("not-json", encoding="utf-8")
    with patch("clockify_cli.config.CONFIG_FILE", config_file):
        cfg = load_config()
    assert cfg.api_key == ""
