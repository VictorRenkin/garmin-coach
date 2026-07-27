from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
SECRETS_DIR = ROOT_DIR / ".secrets"
TOKEN_DIR = SECRETS_DIR / "garmin_tokens"
DATA_DIR = ROOT_DIR / "data"
ACTIVITIES_DIR = DATA_DIR / "activities"
ANALYSES_DIR = DATA_DIR / "analyses"
ACTIVITY_DETAILS_DIR = DATA_DIR / "activity_details"
PLANS_DIR = DATA_DIR / "plans"
REPORTS_DIR = ROOT_DIR / "reports"
STATE_FILE = DATA_DIR / "state.json"
CODEX_STATE_FILE = DATA_DIR / "codex_state.json"
PROFILE_FILE = DATA_DIR / "profile.json"


@dataclass(frozen=True)
class Settings:
    email: str | None
    password: str | None
    poll_seconds: int
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-sol"


def load_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env")
    raw_poll = os.getenv("GARMIN_POLL_SECONDS", "300")
    try:
        poll_seconds = max(60, int(raw_poll))
    except ValueError as exc:
        raise ValueError("GARMIN_POLL_SECONDS doit être un nombre entier.") from exc

    return Settings(
        email=os.getenv("GARMIN_EMAIL") or None,
        password=os.getenv("GARMIN_PASSWORD") or None,
        poll_seconds=poll_seconds,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or "gpt-5.6-sol",
    )


def ensure_directories() -> None:
    # Le dossier précis des jetons ne doit pas être créé avant la première
    # authentification : garth interprète son existence comme une session à charger.
    for directory in (
        SECRETS_DIR,
        ACTIVITIES_DIR,
        ACTIVITY_DETAILS_DIR,
        ANALYSES_DIR,
        PLANS_DIR,
        REPORTS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
