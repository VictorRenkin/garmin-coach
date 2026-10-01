from __future__ import annotations

import os
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
SECRETS_DIR = ROOT_DIR / ".secrets"
TOKEN_DIR = SECRETS_DIR / "garmin_tokens"
GLOBAL_DATA_DIR = ROOT_DIR / "data"
USERS_DATA_DIR = GLOBAL_DATA_DIR / "users"

# All athlete files are resolved from the request context.  Keeping the path
# object dynamic avoids a fragile global "current user" and is safe with the
# threaded HTTP server.
_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "motionest_current_user", default=None
)


def current_user_id() -> str | None:
    return _current_user_id.get()


def _user_root() -> Path:
    user_id = _current_user_id.get()
    if not user_id:
        # This is only used by local CLI/backwards compatibility. Web requests
        # must set a user context before touching athlete data.
        return GLOBAL_DATA_DIR / "legacy"
    return USERS_DATA_DIR / user_id


class _UserPath:
    """Path-like proxy resolving against the active athlete workspace."""

    def __init__(self, relative: str, *, token: bool = False) -> None:
        self.relative = relative
        self.token = token

    def _path(self) -> Path:
        return (SECRETS_DIR / "garmin_tokens" / (_current_user_id.get() or "legacy") / self.relative) if self.token else (_user_root() / self.relative)

    def __truediv__(self, other: object) -> "_UserPath":
        return _UserPath(str(Path(self.relative) / str(other)), token=self.token)

    def __fspath__(self) -> str:
        return str(self._path())

    def __str__(self) -> str:
        return str(self._path())

    def __repr__(self) -> str:
        return repr(self._path())

    def __getattr__(self, name: str):
        return getattr(self._path(), name)


@contextmanager
def user_context(user_id: str):
    """Bind all athlete file access to one opaque user id for this request."""
    if not user_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in user_id):
        raise ValueError("Identifiant utilisateur invalide.")
    marker = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(marker)


DATA_DIR = _UserPath("")
ACTIVITIES_DIR = _UserPath("activities")
ANALYSES_DIR = _UserPath("analyses")
ACTIVITY_DETAILS_DIR = _UserPath("activity_details")
MULTISPORT_DETAILS_DIR = _UserPath("activity_details_multisport")
ACTIVITY_HISTORY_DIR = _UserPath("activity_history")
PLANS_DIR = _UserPath("plans")
REPORTS_DIR = _UserPath("reports")
STATE_FILE = _UserPath("state.json")
CODEX_STATE_FILE = _UserPath("codex_state.json")
PROFILE_FILE = _UserPath("profile.json")
MOTIONEST_INPUTS_FILE = _UserPath("motionest_inputs_v1.json")
GOAL_ASSESSMENT_FILE = _UserPath("goal_assessment_v1.json")
ONBOARDING_FILE = _UserPath("onboarding.json")
HISTORY_IMPORT_FILE = _UserPath("history_import.json")
COACH_MEMORY_FILE = _UserPath("coach_memory.json")
TOKEN_DIR = _UserPath("", token=True)


@dataclass(frozen=True)
class Settings:
    email: str | None
    password: str | None
    poll_seconds: int
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-sol"
    ai_provider: str = "openai"
    ai_base_url: str | None = None

    @property
    def ai_api_key(self) -> str | None:
        return self.openai_api_key

    @property
    def ai_model(self) -> str:
        return self.openai_model


def load_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env", override=True)
    raw_poll = os.getenv("GARMIN_POLL_SECONDS", "300")
    try:
        poll_seconds = max(60, int(raw_poll))
    except ValueError as exc:
        raise ValueError("GARMIN_POLL_SECONDS doit être un nombre entier.") from exc

    provider = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "nvidia" if os.getenv("NVIDIA_API_KEY") else "openai"
    defaults = {
        "openai": (None, "gpt-5.6-sol", "OPENAI_API_KEY"),
        "nvidia": ("https://integrate.api.nvidia.com/v1", "deepseek-ai/deepseek-v4-flash", "NVIDIA_API_KEY"),
        "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "GROQ_API_KEY"),
        "openrouter": ("https://openrouter.ai/api/v1", "openrouter/free", "OPENROUTER_API_KEY"),
        "github": ("https://models.github.ai/inference", "openai/gpt-4.1", "GITHUB_TOKEN"),
        "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.5-flash", "GEMINI_API_KEY"),
        "custom": (None, "", "AI_API_KEY"),
    }
    default_url, default_model, provider_key = defaults.get(provider, defaults["custom"])
    api_key = os.getenv("AI_API_KEY") or os.getenv(provider_key)
    model = os.getenv("AI_MODEL") or os.getenv(
        "NVIDIA_MODEL" if provider == "nvidia" else "OPENAI_MODEL"
    ) or default_model
    base_url = os.getenv("AI_BASE_URL") or os.getenv(
        "NVIDIA_BASE_URL" if provider == "nvidia" else ""
    ) or default_url

    return Settings(
        email=os.getenv("GARMIN_EMAIL") or None,
        password=os.getenv("GARMIN_PASSWORD") or None,
        poll_seconds=poll_seconds,
        openai_api_key=api_key or None,
        openai_model=model,
        ai_provider=provider,
        ai_base_url=base_url,
    )


def ensure_directories() -> None:
    # Le dossier précis des jetons ne doit pas être créé avant la première
    # authentification : garth interprète son existence comme une session à charger.
    for directory in (
        SECRETS_DIR,
        _user_root(),
        ACTIVITIES_DIR,
        ACTIVITY_DETAILS_DIR,
        MULTISPORT_DETAILS_DIR,
        ACTIVITY_HISTORY_DIR,
        ANALYSES_DIR,
        PLANS_DIR,
        REPORTS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
