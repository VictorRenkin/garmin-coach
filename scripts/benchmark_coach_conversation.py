"""Benchmark manuel et isolé du Coach conversationnel.

Ce script n'est jamais appelé par l'application ni par la suite de tests. Il utilise
six messages synthétiques au maximum et redirige messages, mémoire et télémétrie
vers un dossier temporaire supprimé à la fin.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from garmin_coach import ai_usage, athlete_memory, coach_chat
from garmin_coach.config import load_settings


PROMPTS = [
    "Je suis un peu blessé au genou mais je voudrais quand même garder 3 entraînements.",
    "La gêne reste légère, stable et elle va mieux depuis trois jours.",
    "Au fait, depuis hier je commence à ne pas me sentir bien.",
    "Ça va mieux maintenant, je ne me sens plus malade.",
    "Je préfère courir le soir et éviter les séances très tôt.",
    "Avec tout ce que je t'ai dit, quelle est la prochaine question vraiment utile avant de reprendre ?",
]


def _context(messages: list[dict[str, object]], query: str) -> dict[str, object]:
    history = messages
    if len(messages) >= 10:
        history = [
            {"role": "user" if index % 2 else "assistant", "content": f"Ancien échange synthétique {index}"}
            for index in range(42)
        ] + messages
    return {
        "objectif": {"intent": "comeback"},
        "declarations_v1": {
            "availability": {"available_days": ["mardi", "jeudi", "samedi"]}
        },
        "trace_sportive": {"derniers_28_jours": {"courses": 1}},
        "memoire_pertinente": athlete_memory.select_relevant_memory(query),
        "conversation_recente": history[-8:],
    }


def main() -> None:
    settings = load_settings()
    if not settings.ai_api_key:
        raise SystemExit("Benchmark non exécuté : aucune clé IA n'est configurée.")
    with tempfile.TemporaryDirectory(prefix="motionest-coach-benchmark-") as raw:
        root = Path(raw)
        coach_chat.MESSAGES_FILE = root / "messages.json"
        coach_chat.COACH_DRAFTS_DIR = root / "drafts"
        athlete_memory.COACH_MEMORY_FILE = root / "memory.json"
        ai_usage.USAGE_FILE = root / "usage.json"
        messages: list[dict[str, object]] = []
        results: list[dict[str, object]] = []
        for index, prompt in enumerate(PROMPTS, start=1):
            request = coach_chat.submit_message(prompt)
            context = _context(messages, prompt)
            started = time.perf_counter()
            try:
                response = coach_chat.generate_response(request, settings, context)
                coach_chat.store_response(str(request["id"]), response)
                results.append({
                    "call": index,
                    "latency_seconds": round(time.perf_counter() - started, 3),
                    "conversation_state": response.conversation_state,
                    "clarification_topic": response.clarification_topic,
                    "action": response.action,
                    "answer": response.message,
                    "memory_operations": [
                        {
                            "operation": update.operation,
                            "category": update.category,
                            "scope": update.scope,
                            "key": update.key,
                        }
                        for update in response.memory_updates
                    ],
                    "recent_messages_sent": len(context["conversation_recente"]),
                    "memory_items_sent": len(context["memoire_pertinente"]),
                    "payload_bytes": len(json.dumps(
                        {"demande": prompt, "contexte": context},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")),
                })
            except Exception as exc:
                coach_chat.mark_request_failed(str(request["id"]))
                results.append({
                    "call": index,
                    "latency_seconds": round(time.perf_counter() - started, 3),
                    "error": type(exc).__name__,
                })
            messages = [
                {"role": item.get("role"), "content": item.get("content")}
                for item in coach_chat.list_messages()
                if item.get("status") in {"processed", "complete"}
            ]
        usage = []
        if ai_usage.USAGE_FILE.is_file():
            usage = json.loads(ai_usage.USAGE_FILE.read_text(encoding="utf-8"))
        print(json.dumps({
            "calls": results,
            "usage": usage,
            "active_memory": athlete_memory.load_memory(include_inactive=False),
            "all_memory_count": len(athlete_memory.load_memory()),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
