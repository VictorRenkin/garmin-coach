from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import GLOBAL_DATA_DIR


TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
OUTBOX_DIR = GLOBAL_DATA_DIR / "outbox"


def production_mode() -> bool:
    return os.getenv("MOTIONEST_ENV", "development").strip().lower() == "production"


def public_auth_config(*, local_bypass: bool = False) -> dict[str, Any]:
    site_key = os.getenv("TURNSTILE_SITE_KEY", "").strip()
    return {
        "turnstileRequired": production_mode() and not local_bypass,
        "turnstileSiteKey": site_key,
    }


def verify_human(
    token: str,
    remote_ip: str = "",
    honeypot: str = "",
    *,
    local_bypass: bool = False,
) -> bool:
    if honeypot:
        return False
    if local_bypass:
        return True
    secret = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    if not production_mode() and not secret:
        return token == "development-pass"
    if not secret or not token or len(token) > 2048:
        return False
    payload = urlencode(
        {"secret": secret, "response": token, "remoteip": remote_ip}
    ).encode("utf-8")
    request = Request(TURNSTILE_VERIFY_URL, data=payload, method="POST")
    try:
        with urlopen(request, timeout=8) as response:  # noqa: S310 - fixed Cloudflare URL
            result = json.loads(response.read())
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(result.get("success"))


def _public_url(path: str, token: str) -> str:
    base = os.getenv("MOTIONEST_PUBLIC_URL", "http://127.0.0.1:8765").rstrip("/")
    return f"{base}{path}?{urlencode({'token': token})}"


def send_account_email(email: str, kind: str, token: str) -> str:
    if kind == "verify":
        subject = "Confirme ton adresse Motionest"
        url = _public_url("/verify-email", token)
        text = (
            "Bienvenue sur Motionest. Confirme ton adresse en ouvrant ce lien "
            f"dans les 24 heures :\n\n{url}\n"
        )
    elif kind == "reset":
        subject = "Réinitialise ton mot de passe Motionest"
        url = _public_url("/reset-password", token)
        text = (
            "Une réinitialisation de ton mot de passe Motionest a été demandée. "
            f"Ce lien reste valable une heure :\n\n{url}\n\n"
            "Ignore ce message si tu n'es pas à l'origine de la demande."
        )
    else:
        raise ValueError("Type d’e-mail inconnu.")

    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-{kind}.txt"
        (OUTBOX_DIR / filename).write_text(
            f"To: {email}\nSubject: {subject}\n\n{text}", encoding="utf-8"
        )
        return url

    message = EmailMessage()
    message["To"] = email
    message["From"] = os.getenv("SMTP_FROM", "Motionest <no-reply@motionest.app>")
    message["Subject"] = subject
    message.set_content(text)
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    with smtplib.SMTP(host, port, timeout=10) as client:
        client.starttls(context=ssl.create_default_context())
        if username:
            client.login(username, password)
        client.send_message(message)
    return url
