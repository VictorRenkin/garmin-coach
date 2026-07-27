from __future__ import annotations

import getpass

from dotenv import set_key

from .config import ROOT_DIR


def main() -> int:
    print("Configuration privée de l'analyse IA OpenAI")
    print("La clé sera enregistrée uniquement dans le fichier local .env ignoré par Git.")
    api_key = getpass.getpass("Clé API OpenAI : ").strip()
    if not api_key:
        print("Configuration annulée : aucune clé fournie.")
        return 1
    if not api_key.startswith("sk-"):
        print("Clé refusée : une clé API OpenAI commence normalement par 'sk-'.")
        return 1

    env_file = ROOT_DIR / ".env"
    set_key(str(env_file), "OPENAI_API_KEY", api_key, quote_mode="always")
    set_key(str(env_file), "OPENAI_MODEL", "gpt-5.6-sol", quote_mode="always")
    print("IA CONFIGURÉE : OUI")
    print("Tu peux maintenant lancer : connexion-garmin.cmd puis l'analyse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
