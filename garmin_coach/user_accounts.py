"""Identités web et sessions isolées des athlètes Motionest.

Le compte Motionest est indépendant des intégrations sportives. Les mots de
passe sont dérivés avec scrypt et les sessions ne contiennent qu'un identifiant
opaque. Les adresses e-mail ne servent jamais de chemin de stockage.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from http.cookies import SimpleCookie
from typing import Any

from .config import GLOBAL_DATA_DIR, SECRETS_DIR


USERS_FILE = GLOBAL_DATA_DIR / "users.json"
SESSION_KEY_FILE = SECRETS_DIR / "web_session.key"
SESSION_TTL_SECONDS = 8 * 60 * 60
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 1024
PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1
EMAIL_TOKEN_TTL_SECONDS = 24 * 60 * 60
RESET_TOKEN_TTL_SECONDS = 60 * 60
TERMS_VERSION = "2026-09-28"
_LOCK = threading.RLock()
_WORKSPACE_LOCKS: dict[str, threading.Lock] = {}
_USER_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _valid_user_id(user_id: str) -> bool:
    return bool(user_id) and len(user_id) <= 80 and all(
        char in _USER_ID_CHARS for char in user_id
    )


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if (
        not normalized
        or len(normalized) > 254
        or normalized.count("@") != 1
        or any(char.isspace() for char in normalized)
    ):
        raise ValueError("Adresse e-mail invalide.")
    local, domain = normalized.split("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Adresse e-mail invalide.")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères."
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError("Le mot de passe est trop long.")


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=PASSWORD_SCRYPT_R,
        p=PASSWORD_SCRYPT_P,
        dklen=32,
    )


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_account(
    email: str, password: str, *, terms_accepted: bool = False
) -> dict[str, Any]:
    """Crée un compte Motionest autonome et retourne son identité publique."""
    normalized = _normalize_email(email)
    _validate_password(password)
    if not terms_accepted:
        raise ValueError("Tu dois accepter les conditions d’utilisation.")
    with _LOCK:
        users = _load_users()
        if any(str(item.get("email", "")).lower() == normalized for item in users):
            raise ValueError("Un compte Motionest existe déjà avec cette adresse.")
        salt = secrets.token_bytes(16)
        verification_token = secrets.token_urlsafe(32)
        user = {
            "email": normalized,
            "id": "u_" + secrets.token_urlsafe(18).replace("-", "_"),
            "password_salt": _urlsafe_encode(salt),
            "password_hash": _urlsafe_encode(_password_digest(password, salt)),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "connections": 0,
            "activities_imported": 0,
            "onboarding_complete": False,
            "email_verified": False,
            "email_verification_hash": _token_digest(verification_token),
            "email_verification_expires": int(time.time()) + EMAIL_TOKEN_TTL_SECONDS,
            "terms_version": TERMS_VERSION,
            "terms_accepted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        users.append(user)
        _save_users(users)
        return {
            "id": user["id"],
            "email": normalized,
            "verificationToken": verification_token,
        }


def authenticate_account(email: str, password: str) -> dict[str, Any] | None:
    """Vérifie les identifiants Motionest avec une comparaison constante."""
    password_length_valid = len(password) <= PASSWORD_MAX_LENGTH
    try:
        normalized = _normalize_email(email)
    except ValueError:
        normalized = "invalid@example.invalid"
    with _LOCK:
        user = next(
            (
                item
                for item in _load_users()
                if str(item.get("email", "")).lower() == normalized
            ),
            None,
        )
    try:
        salt = _urlsafe_decode(str(user.get("password_salt") or "")) if user else b""
        expected = _urlsafe_decode(str(user.get("password_hash") or "")) if user else b""
    except (ValueError, TypeError):
        salt, expected = b"", b""
    if len(salt) != 16 or len(expected) != 32:
        salt, expected = b"\0" * 16, b"\0" * 32
    supplied = _password_digest(password[:PASSWORD_MAX_LENGTH], salt)
    if (
        user is None
        or not password_length_valid
        or not hmac.compare_digest(supplied, expected)
    ):
        return None
    user_id = str(user.get("id") or "")
    if not _valid_user_id(user_id):
        return None
    return {
        "id": user_id,
        "email": str(user.get("email") or ""),
        "emailVerified": bool(user.get("email_verified")),
    }


def issue_email_verification(email: str) -> tuple[str, str] | None:
    """Renouvelle le lien de confirmation d'un compte non confirmé."""
    normalized = _normalize_email(email)
    with _LOCK:
        users = _load_users()
        user = next(
            (item for item in users if str(item.get("email", "")).lower() == normalized),
            None,
        )
        if user is None or bool(user.get("email_verified")):
            return None
        token = secrets.token_urlsafe(32)
        user["email_verification_hash"] = _token_digest(token)
        user["email_verification_expires"] = int(time.time()) + EMAIL_TOKEN_TTL_SECONDS
        _save_users(users)
        return normalized, token


def confirm_email(token: str) -> bool:
    if not token or len(token) > 512:
        return False
    digest = _token_digest(token)
    now = int(time.time())
    with _LOCK:
        users = _load_users()
        user = next(
            (
                item
                for item in users
                if hmac.compare_digest(
                    str(item.get("email_verification_hash") or ""), digest
                )
            ),
            None,
        )
        if user is None or int(user.get("email_verification_expires") or 0) < now:
            return False
        user["email_verified"] = True
        user["email_verified_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        user.pop("email_verification_hash", None)
        user.pop("email_verification_expires", None)
        _save_users(users)
        return True


def issue_password_reset(email: str) -> tuple[str, str] | None:
    """Crée un lien de récupération sans révéler l'existence du compte à l'API."""
    try:
        normalized = _normalize_email(email)
    except ValueError:
        return None
    with _LOCK:
        users = _load_users()
        user = next(
            (item for item in users if str(item.get("email", "")).lower() == normalized),
            None,
        )
        if user is None or not bool(user.get("email_verified")):
            return None
        token = secrets.token_urlsafe(32)
        user["password_reset_hash"] = _token_digest(token)
        user["password_reset_expires"] = int(time.time()) + RESET_TOKEN_TTL_SECONDS
        _save_users(users)
        return normalized, token


def reset_password(token: str, password: str) -> bool:
    _validate_password(password)
    if not token or len(token) > 512:
        return False
    digest = _token_digest(token)
    now = int(time.time())
    with _LOCK:
        users = _load_users()
        user = next(
            (
                item
                for item in users
                if hmac.compare_digest(str(item.get("password_reset_hash") or ""), digest)
            ),
            None,
        )
        if user is None or int(user.get("password_reset_expires") or 0) < now:
            return False
        salt = secrets.token_bytes(16)
        user["password_salt"] = _urlsafe_encode(salt)
        user["password_hash"] = _urlsafe_encode(_password_digest(password, salt))
        user["password_changed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        user.pop("password_reset_hash", None)
        user.pop("password_reset_expires", None)
        _save_users(users)
        return True


def _load_users() -> list[dict[str, Any]]:
    try:
        payload = (
            json.loads(USERS_FILE.read_text(encoding="utf-8"))
            if USERS_FILE.is_file()
            else []
        )
    except (OSError, json.JSONDecodeError):
        payload = []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _save_users(users: list[dict[str, Any]]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = USERS_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(USERS_FILE)


def user_id_for_email(email: str) -> tuple[str, bool]:
    """Retourne un id opaque sans créer de compte avant le succès Garmin."""
    normalized = _normalize_email(email)
    with _LOCK:
        user = next(
            (
                item
                for item in _load_users()
                if str(item.get("email", "")).lower() == normalized
            ),
            None,
        )
        if user and _valid_user_id(str(user.get("id") or "")):
            return str(user["id"]), False
        # Aléatoire, opaque, et non persisté avant une authentification réussie.
        return "u_" + secrets.token_urlsafe(18).replace("-", "_"), True


def ensure_user_record(email: str, proposed_user_id: str) -> tuple[str, bool]:
    """Crée l'identité après Garmin et retourne l'id canonique atomiquement.

    Deux connexions simultanées du même nouveau compte convergent ainsi vers le
    même espace au lieu de produire deux sessions pointant vers deux dossiers.
    """
    normalized = _normalize_email(email)
    if not _valid_user_id(proposed_user_id):
        raise ValueError("Identifiant utilisateur invalide.")
    with _LOCK:
        users = _load_users()
        user = next(
            (
                item
                for item in users
                if str(item.get("email", "")).lower() == normalized
            ),
            None,
        )
        created = user is None
        if user is None:
            user = {
                "email": normalized,
                "id": proposed_user_id,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "connections": 0,
                "activities_imported": 0,
            }
            users.append(user)
        canonical = str(user.get("id") or "")
        if not _valid_user_id(canonical):
            canonical = proposed_user_id
            user["id"] = canonical
        _save_users(users)
        return canonical, created


def record_login(
    email: str,
    user_id: str,
    *,
    imported: int,
    onboarding_complete: bool,
) -> None:
    """Met à jour les statistiques du compte sous le même verrou que sa création."""
    normalized = _normalize_email(email)
    if not _valid_user_id(user_id):
        raise ValueError("Identifiant utilisateur invalide.")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _LOCK:
        users = _load_users()
        user = next(
            (
                item
                for item in users
                if str(item.get("email", "")).lower() == normalized
            ),
            None,
        )
        if user is None:
            raise ValueError("Compte utilisateur introuvable après authentification.")
        # Ne permet jamais à un appel concurrent de remplacer l'espace canonique.
        if str(user.get("id") or "") != user_id:
            raise ValueError("Session utilisateur incohérente.")
        user["last_login"] = now
        user["connections"] = int(user.get("connections") or 0) + 1
        user["activities_imported"] = int(user.get("activities_imported") or 0) + max(
            0, int(imported)
        )
        user["onboarding_complete"] = bool(onboarding_complete)
        _save_users(users)


def list_user_records() -> list[dict[str, Any]]:
    """Retourne un instantané détaché pour l'agrégation manager."""
    with _LOCK:
        return json.loads(json.dumps(_load_users()))


@contextmanager
def user_login_lock(user_id: str):
    """Sérialise synchronisation et écriture des jetons d'un même compte."""
    if not _valid_user_id(user_id):
        raise ValueError("Identifiant utilisateur invalide.")
    with _LOCK:
        lock = _WORKSPACE_LOCKS.setdefault(user_id, threading.Lock())
    with lock:
        yield


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_key() -> bytes:
    with _LOCK:
        try:
            key = _urlsafe_decode(SESSION_KEY_FILE.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            key = b""
        if len(key) >= 32:
            return key
        key = secrets.token_bytes(32)
        SESSION_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_KEY_FILE.write_text(_urlsafe_encode(key), encoding="ascii")
        return key


def create_session(user_id: str, *, remember: bool = False) -> str:
    """Crée une session signée qui reste valide après un redémarrage."""
    if not _valid_user_id(user_id):
        raise ValueError("Identifiant utilisateur invalide.")
    expires = int(time.time()) + (30 * 24 * 60 * 60 if remember else SESSION_TTL_SECONDS)
    payload = f"{user_id}:{expires}:{secrets.token_urlsafe(16)}".encode("ascii")
    encoded = _urlsafe_encode(payload)
    signature = hmac.new(_session_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_urlsafe_encode(signature)}"


def _cookie_value(header: str, name: str) -> str:
    try:
        cookie = SimpleCookie()
        cookie.load(header)
        return cookie[name].value if name in cookie else ""
    except (KeyError, ValueError):
        return ""


def user_from_cookie(cookie: str) -> str | None:
    token = _cookie_value(cookie, "motionest_session")
    encoded, separator, supplied_signature = token.partition(".")
    if not separator or not encoded or not supplied_signature:
        return None
    expected = hmac.new(
        _session_key(), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        signature = _urlsafe_decode(supplied_signature)
        payload = _urlsafe_decode(encoded).decode("ascii")
        user_id, expires_text, _nonce = payload.split(":", 2)
        expires = int(expires_text)
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(signature, expected):
        return None
    if expires < int(time.time()) or not _valid_user_id(user_id):
        return None
    return user_id


def session_cookie(token: str, *, remember: bool = False) -> str:
    attributes = [
        f"motionest_session={token}",
        "HttpOnly",
        "SameSite=Strict",
        "Path=/",
    ]
    if remember:
        attributes.append(f"Max-Age={30 * 24 * 60 * 60}")
    if os.getenv("MOTIONEST_ENV", "development").strip().lower() == "production":
        attributes.append("Secure")
    return "; ".join(attributes)


def public_user_identifier(email: str) -> str:
    """Aide de compatibilité pour les tests; ne jamais l'utiliser comme chemin."""
    return hashlib.sha256(_normalize_email(email).encode()).hexdigest()[:12]
