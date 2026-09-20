"""Plumbing, not learning material. Loads .env and hands back model IDs.

Deliberately boring: every interesting decision about the API belongs in a level
exercise, not in here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


def api_key() -> str:
    """The Anthropic key, or a clear error saying how to fix it."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key == "sk-ant-REPLACE_ME":
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and put your real key "
            "in it. Do not commit .env."
        )
    return key


def default_model() -> str:
    """Model for graded runs and final eval sweeps."""
    return os.environ.get("DEFAULT_MODEL", "claude-opus-5")


def cheap_model() -> str:
    """Model for fast iteration loops. See PLAN.md section 8."""
    return os.environ.get("CHEAP_MODEL", "claude-haiku-4-5")


def has_api_key() -> bool:
    """True if integration tests can run. Used to skip, not to fail."""
    try:
        api_key()
    except RuntimeError:
        return False
    return True
