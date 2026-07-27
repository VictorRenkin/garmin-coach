from __future__ import annotations

import getpass
from collections.abc import Callable

from garminconnect import Garmin

from .config import Settings, TOKEN_DIR, ensure_directories


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
        client.login(str(TOKEN_DIR))
        return client

    email = settings.email or input_fn("Adresse e-mail Garmin Connect : ").strip()
    password = settings.password or password_fn("Mot de passe Garmin Connect : ")
    if not email or not password:
        raise ValueError("L'adresse e-mail et le mot de passe Garmin sont requis.")

    def ask_mfa() -> str:
        return input_fn("Code de vérification Garmin : ").strip()

    client = Garmin(email, password, prompt_mfa=ask_mfa)
    # Première connexion sans tokenstore, puis sauvegarde de la session réussie.
    client.login()
    client.garth.dump(str(TOKEN_DIR))
    return client
