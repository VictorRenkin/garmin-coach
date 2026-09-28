from __future__ import annotations

import getpass

from dotenv import set_key

from .config import ROOT_DIR


def main() -> int:
    print("Configuration privée du fournisseur d'IA")
    print("La clé sera enregistrée uniquement dans le fichier local .env ignoré par Git.")
    choices = {
        "openai": ("gpt-5.6-sol", ""),
        "nvidia": ("deepseek-ai/deepseek-v4-flash", "https://integrate.api.nvidia.com/v1"),
        "groq": ("openai/gpt-oss-120b", "https://api.groq.com/openai/v1"),
        "openrouter": ("openrouter/free", "https://openrouter.ai/api/v1"),
        "github": ("openai/gpt-4.1", "https://models.github.ai/inference"),
        "gemini": ("gemini-3.5-flash", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        "custom": ("", ""),
    }
    provider = input(
        "Fournisseur (openai, nvidia, groq, openrouter, github, gemini, custom) [nvidia] : "
    ).strip().lower() or "nvidia"
    if provider not in choices:
        print("Fournisseur inconnu.")
        return 1
    default_model, default_url = choices[provider]
    api_key = getpass.getpass("Clé API : ").strip()
    if not api_key:
        print("Configuration annulée : aucune clé fournie.")
        return 1
    if len(api_key) < 12:
        print("Clé refusée : elle semble trop courte.")
        return 1

    model = input(f"Modèle [{default_model}] : ").strip() or default_model
    base_url = default_url
    if provider == "custom":
        base_url = input("URL API compatible OpenAI (https://...) : ").strip()
    if not model or (provider == "custom" and not base_url.startswith("https://")):
        print("Le modèle et une URL HTTPS valide sont requis.")
        return 1

    env_file = ROOT_DIR / ".env"
    set_key(str(env_file), "AI_PROVIDER", provider, quote_mode="always")
    set_key(str(env_file), "AI_API_KEY", api_key, quote_mode="always")
    set_key(str(env_file), "AI_MODEL", model, quote_mode="always")
    set_key(str(env_file), "AI_BASE_URL", base_url, quote_mode="always")
    print("IA CONFIGURÉE : OUI")
    print("Tu peux maintenant lancer : connexion-garmin.cmd puis l'analyse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
