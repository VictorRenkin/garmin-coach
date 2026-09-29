from __future__ import annotations

import getpass
import json
import logging
from collections.abc import Callable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .config import Settings, TOKEN_DIR, ensure_directories


class GarminMFARequiredError(ValueError):
    """La connexion web ne peut pas encore reprendre le flux MFA Garmin."""


def stored_tokens_available() -> bool:
    """Indique si le contexte utilisateur contient deux jetons Garmin lisibles."""
    required_fields = {
        "oauth1_token.json": ("oauth_token", "oauth_token_secret"),
        "oauth2_token.json": ("access_token", "refresh_token"),
    }
    for filename, fields in required_fields.items():
        path = TOKEN_DIR / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict) or any(
            not isinstance(payload.get(field), str) or not payload[field].strip()
            for field in fields
        ):
            return False
    return True


def _exception_messages(exc: BaseException) -> str:
    messages: list[str] = []
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    return " | ".join(messages)


def _normalize_login_error(exc: GarminConnectConnectionError) -> None:
    """Corrige les erreurs SSO que garminconnect classe parfois comme réseau."""
    message = _exception_messages(exc)
    # Certaines versions de garth encapsulent le statut HTTP dans le texte de
    # l'erreur au lieu de l'exposer sur ``response.status_code``. Dans ce cas,
    # garminconnect le transforme à tort en erreur réseau générique.
    rate_limit_markers = (
        "429 client error",
        "status=429",
        "too many requests",
        "rate limit exceeded",
    )
    if any(marker in message for marker in rate_limit_markers):
        raise GarminConnectTooManyRequestsError(
            "Garmin bloque temporairement les nouvelles tentatives de connexion."
        ) from exc
    if "validation en deux étapes garmin" in message:
        raise GarminMFARequiredError(
            "La validation en deux étapes Garmin doit être confirmée."
        ) from exc
    authentication_markers = (
        "unauthorized",
        "authentication failed",
        "invalid credentials",
        "incorrect password",
        "unexpected title: garmin: sign in",
        "unexpected title: garmin | sign in",
        "unexpected title: sign in",
        "unexpected title: signin",
        "unexpected title: login",
    )
    if any(marker in message for marker in authentication_markers):
        raise GarminConnectAuthenticationError("Authentication failed") from exc
    raise exc


def _quiet_login(client: Garmin, token_dir: str | None = None) -> None:
    """Empêche la bibliothèque Garmin d'imprimer une trace complète à l'écran."""
    logger = logging.getLogger("garminconnect")
    previous_disabled = logger.disabled
    logger.disabled = True
    try:
        client.login(token_dir) if token_dir else client.login()
    finally:
        logger.disabled = previous_disabled


def connect(
    settings: Settings,
    input_fn: Callable[[str], str] = input,
    password_fn: Callable[[str], str] = getpass.getpass,
) -> Garmin:
    """Ouvre une session Garmin et mémorise uniquement ses jetons locaux."""
    ensure_directories()

    oauth1_token = TOKEN_DIR / "oauth1_token.json"
    oauth2_token = TOKEN_DIR / "oauth2_token.json"

    # Les deux fichiers sont requis pour considérer la session comme complète.
    if oauth1_token.is_file() and oauth2_token.is_file():
        client = Garmin()
        _quiet_login(client, str(TOKEN_DIR))
        return client

    email = settings.email or input_fn("Adresse e-mail Garmin Connect : ").strip()
    password = settings.password or password_fn("Mot de passe Garmin Connect : ")
    if not email or not password:
        raise ValueError("L'adresse e-mail et le mot de passe Garmin sont requis.")

    def ask_mfa() -> str:
        return input_fn("Code de vérification Garmin : ").strip()

    client = Garmin(email, password, prompt_mfa=ask_mfa)
    # Première connexion sans tokenstore, puis sauvegarde de la session réussie.
    _quiet_login(client)
    client.garth.dump(str(TOKEN_DIR))
    return client


def authenticate_with_credentials(
    email: str, password: str, mfa_code: str | None = None
) -> Garmin:
    """Authentifie Garmin sans encore écrire de jetons sur disque."""
    def require_mfa() -> str:
        if mfa_code and mfa_code.strip():
            return mfa_code.strip()
        raise GarminMFARequiredError(
            "La validation en deux étapes Garmin doit être confirmée."
        )

    client = Garmin(email, password, prompt_mfa=require_mfa)
    try:
        _quiet_login(client)
    except GarminConnectConnectionError as exc:
        _normalize_login_error(exc)
    return client


def persist_client_tokens(client: Garmin) -> None:
    """Enregistre les jetons dans l'espace utilisateur déjà authentifié."""
    ensure_directories()
    client.garth.dump(str(TOKEN_DIR))


def connect_with_credentials(email: str, password: str) -> Garmin:
    """Compatibilité CLI : authentifie puis persiste dans le contexte actif."""
    client = authenticate_with_credentials(email, password)
    persist_client_tokens(client)
    return client
