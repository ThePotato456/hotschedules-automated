"""Configuration and secret loading.

Secrets come from environment variables, optionally seeded from a local
``secrets.env`` file (git-ignored). Never hard-code credentials.
"""
from __future__ import annotations
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / "secrets.env"


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Load KEY=VALUE lines from an env file into os.environ (without overriding
    values already set in the real environment)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_credentials() -> tuple[str, str]:
    load_env_file()
    user = os.environ.get("HS_USERNAME")
    pwd = os.environ.get("HS_PASSWORD")
    if not user or not pwd:
        raise RuntimeError(
            "Missing HS_USERNAME / HS_PASSWORD. Set them in secrets.env or the environment."
        )
    return user, pwd


# Store-local timezone (from scheduling/bootstrap: America/Chicago).
STORE_TZ = os.environ.get("HS_STORE_TZ", "America/Chicago")
BASE_URL = "https://app.hotschedules.com/hs"


def get_delivery_config() -> dict:
    """Discord + uploader settings (loaded from secrets.env / environment)."""
    load_env_file()
    token = os.environ.get("DISCORD_BOT_TOKEN")
    recipient = os.environ.get("DISCORD_RECIPIENT_ID")
    if not token or not recipient:
        raise RuntimeError(
            "Missing DISCORD_BOT_TOKEN / DISCORD_RECIPIENT_ID in secrets.env or environment."
        )
    return {
        "token": token,
        "recipient_id": recipient,
        "uploader_base": os.environ.get("UPLOADER_BASE_URL", "https://files.example.com"),
    }
