from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from typing import Any

from .ai_usage import usage_summary
from .config import GLOBAL_DATA_DIR, SECRETS_DIR, USERS_DATA_DIR, load_settings
from .user_accounts import list_user_records


ACCOUNT_FILE = SECRETS_DIR / "manager_account.json"
MANAGER_EMAIL = "manager@motionest.com"
MANAGER_SESSION_TTL_SECONDS = 8 * 60 * 60
_LOCK = threading.RLock()


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return base64.b64encode(digest).decode()


def ensure_manager_account() -> None:
    """Refuse un compte implicite plutôt que d'écrire un mot de passe en clair."""
    if ACCOUNT_FILE.is_file():
        return
    raise RuntimeError(
        "Compte manager non configuré. Utilise set_manager_account une fois en local."
    )


def set_manager_account(email: str, password: str) -> None:
    """Remplace le compte manager en ne conservant qu'un dérivé du mot de passe."""
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized or len(password) < 12:
        raise ValueError("Compte manager invalide.")
    salt = secrets.token_bytes(16)
    payload = {
        "email": normalized,
        "salt": base64.b64encode(salt).decode(),
        "password_hash": _hash_password(password, salt),
    }
    with _LOCK:
        ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = ACCOUNT_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(ACCOUNT_FILE)


def _load_account() -> dict[str, str]:
    ensure_manager_account()
    payload = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    required = {"email", "salt", "password_hash"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise RuntimeError("Compte manager corrompu.")
    return {name: str(payload[name]) for name in required}


def _session_key(account: dict[str, str]) -> bytes:
    material = f"{account['salt']}:{account['password_hash']}".encode("ascii")
    return hashlib.sha256(material).digest()


def _sign_manager_session(account: dict[str, str]) -> str:
    expires = int(time.time()) + MANAGER_SESSION_TTL_SECONDS
    payload = f"{expires}:{secrets.token_urlsafe(18)}"
    signature = hmac.new(
        _session_key(account), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def manager_login(email: str, password: str) -> str | None:
    account = _load_account()
    salt = base64.b64decode(account["salt"])
    valid = hmac.compare_digest(
        str(email).strip().lower(), account["email"].lower()
    ) and hmac.compare_digest(
        _hash_password(password, salt), account["password_hash"]
    )
    if not valid:
        return None
    return _sign_manager_session(account)


def manager_authorized(cookie: str) -> bool:
    try:
        parsed = SimpleCookie()
        parsed.load(cookie)
        token = parsed["motionest_manager"].value
        payload, separator, supplied = token.rpartition(".")
        expires_text, nonce_separator, _nonce = payload.partition(":")
        if not separator or not nonce_separator or int(expires_text) < int(time.time()):
            return False
        account = _load_account()
        expected = hmac.new(
            _session_key(account), payload.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(supplied, expected)
    except (KeyError, ValueError, OSError, json.JSONDecodeError, RuntimeError):
        return False


def manager_cookie(token: str) -> str:
    return (
        f"motionest_manager={token}; HttpOnly; SameSite=Strict; "
        f"Path=/; Max-Age={MANAGER_SESSION_TTL_SECONDS}"
    )


def manager_stats() -> dict[str, Any]:
    users = [
        user
        for user in list_user_records()
        if 0 < len(str(user.get("id") or "")) <= 80
        and all(
            char.isascii() and (char.isalnum() or char in "_-")
            for char in str(user.get("id") or "")
        )
    ]
    files = [item for item in GLOBAL_DATA_DIR.rglob("*") if item.is_file()]
    activity_count = 0
    message_count = 0
    usage_files = []
    for user in users:
        user_id = str(user.get("id") or "")
        # Les ids valides sont opaques et ne peuvent pas sortir de USERS_DATA_DIR.
        root = USERS_DATA_DIR / user_id
        activity_count += len(list((root / "activities").glob("*.json")))
        messages_file = root / "coach_messages.json"
        if messages_file.is_file():
            try:
                payload = json.loads(messages_file.read_text(encoding="utf-8"))
                message_count += len(payload) if isinstance(payload, list) else 0
            except (OSError, json.JSONDecodeError):
                pass
        usage_files.append(root / "ai_usage.json")
    settings = load_settings()
    return {
        "users": users,
        "userCount": len(users),
        "activityCount": activity_count,
        "messageCount": message_count,
        "storageBytes": sum(item.stat().st_size for item in files),
        "ai": {
            "configured": bool(settings.ai_api_key),
            "provider": settings.ai_provider if settings.ai_api_key else None,
            "model": settings.ai_model if settings.ai_api_key else None,
            "remainingTokens": None,
            "note": (
                "Le solde de crédits nécessite une API de facturation autorisée."
                if settings.ai_api_key
                else "Aucune clé IA configurée."
            ),
        },
        "usage": usage_summary(usage_files),
    }
