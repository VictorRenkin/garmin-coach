from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

from .config import DATA_DIR, Settings, ensure_directories


MESSAGES_FILE = DATA_DIR / "coach_messages.json"
COACH_DRAFTS_DIR = DATA_DIR / "coach_drafts"
_LOCK = threading.Lock()


class ObjectiveUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=80)
    distance_km: float | None = Field(default=None, ge=1, le=200)
    sessions_per_week: int | None = Field(default=None, ge=1, le=14)
    race_date: str | None = None


class CoachResponse(BaseModel):
    message: str = Field(min_length=2, max_length=3000)
    plan_modifie: bool = False
    action: Literal["none", "update_objective", "reschedule_session"] = "none"
    objective_update: ObjectiveUpdate | None = None
    session_index: int | None = Field(default=None, ge=0)
    new_date: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "CoachResponse":
        if self.action == "update_objective" and self.objective_update is None:
            raise ValueError("Une mise à jour d'objectif est requise.")
        if self.action == "reschedule_session" and (
            self.session_index is None or self.new_date is None
        ):
            raise ValueError("La séance et sa nouvelle date sont requises.")
        return self


COACH_CHAT_INSTRUCTIONS = """
Tu es le coach prudent d'une application Garmin. Réponds en français, clairement,
en moins de 180 mots. Le contexte contient l'objectif, le plan, les métriques
Garmin anonymisées et les échanges récents du même chat. N'invente aucune donnée,
douleur, fatigue ou séance.

Tu peux répondre aux questions sur l'entraînement, la récupération, les objectifs,
la charge, les séances manquées et la préparation d'une course. Après une nouvelle
course ou quand le ressenti manque, pose une seule question courte et utile, par
exemple sur l'effort perçu, la fatigue ou une douleur. Réutilise la réponse dans
les échanges suivants. Tu peux aussi proposer une action structurée, qui sera
toujours confirmée dans l'application :
- update_objective si la demande contient une modification explicite du titre,
  de la distance, du nombre de séances ou de la date de course ;
- reschedule_session uniquement si l'utilisateur donne l'index exact de la séance
  (la première vaut 0) et une date ISO YYYY-MM-DD ;
- none dans tous les autres cas, notamment si une précision est nécessaire.
Ne prétends jamais qu'une action a déjà été appliquée. plan_modifie reste false.
""".strip()


def _load_unlocked() -> list[dict[str, Any]]:
    if not MESSAGES_FILE.is_file():
        return []
    try:
        payload = json.loads(MESSAGES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _save_unlocked(messages: list[dict[str, Any]]) -> None:
    ensure_directories()
    COACH_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    MESSAGES_FILE.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_messages() -> list[dict[str, Any]]:
    with _LOCK:
        return _load_unlocked()


def get_message(message_id: str) -> dict[str, Any] | None:
    return next((item for item in list_messages() if item.get("id") == message_id), None)


def mark_action_applied(message_id: str) -> None:
    with _LOCK:
        messages = _load_unlocked()
        message = next((item for item in messages if item.get("id") == message_id), None)
        if message is None:
            raise ValueError("Réponse du coach introuvable.")
        message["action_status"] = "applied"
        _save_unlocked(messages)


def add_assistant_prompt(content: str, prompt_key: str) -> dict[str, Any]:
    """Ajoute une question proactive une seule fois dans le fil du coach."""
    with _LOCK:
        messages = _load_unlocked()
        existing = next(
            (item for item in messages if item.get("prompt_key") == prompt_key), None
        )
        if existing is not None:
            return existing
        prompt = {
            "id": uuid.uuid4().hex[:12],
            "role": "assistant",
            "content": content,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "complete",
            "action": "none",
            "prompt_key": prompt_key,
        }
        messages.append(prompt)
        _save_unlocked(messages)
    return prompt


def submit_message(content: str) -> dict[str, Any]:
    normalized = " ".join(content.strip().split())
    if not normalized:
        raise ValueError("Écris une demande avant de l'envoyer.")
    if len(normalized) > 2000:
        raise ValueError("La demande ne peut pas dépasser 2 000 caractères.")
    message = {
        "id": uuid.uuid4().hex[:12],
        "role": "user",
        "content": normalized,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "pending",
    }
    with _LOCK:
        messages = _load_unlocked()
        messages.append(message)
        _save_unlocked(messages)
    return message


def pending_requests() -> list[dict[str, Any]]:
    return [
        item
        for item in list_messages()
        if item.get("role") == "user" and item.get("status") == "pending"
    ]


def generate_response(
    request: dict[str, Any], settings: Settings, context: dict[str, Any]
) -> CoachResponse:
    """Produit la réponse affichée par le tableau de bord via l'API configurée."""
    if not settings.openai_api_key:
        return local_response(request["content"], context)
    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=12.0, max_retries=0)
        response = client.responses.parse(
            model=settings.openai_model,
            reasoning={"effort": "none"},
            instructions=COACH_CHAT_INSTRUCTIONS,
            input=json.dumps(
                {"demande": request["content"], "contexte": context},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            text_format=CoachResponse,
        )
        if response.output_parsed is not None:
            return response.output_parsed
    except Exception:
        pass
    return local_response(request["content"], context)


def local_response(content: str, context: dict[str, Any]) -> CoachResponse:
    """Réponse locale immédiate pour les usages courants et les pannes d'API."""
    text = " ".join(content.lower().split())
    if any(word in text for word in ("douleur", "bless", "genou", "cheville")):
        return CoachResponse(message="Ne cours pas malgré une douleur. Où se situe-t-elle et apparaît-elle au repos ou seulement pendant l'effort ? Si elle est importante, persistante ou inhabituelle, demande un avis professionnel.")

    sessions = re.search(r"\b([1-7])\s*s[ée]ances?", text)
    if sessions:
        count = int(sessions.group(1))
        return CoachResponse(
            message=f"Je peux passer l'objectif à {count} séances par semaine. Confirme la proposition ci-dessous pour l'appliquer.",
            action="update_objective",
            objective_update=ObjectiveUpdate(sessions_per_week=count),
        )

    distance = re.search(r"\b(5|10|21[,.]1|42[,.]2)\s*km\b", text)
    if distance and any(word in text for word in ("objectif", "prépar", "course")):
        km = float(distance.group(1).replace(",", "."))
        title = "Terminer un semi-marathon" if km == 21.1 else f"Préparer un {km:g} km"
        return CoachResponse(
            message=f"Je te propose de définir {km:g} km comme nouvel objectif. La charge évoluera progressivement après confirmation.",
            action="update_objective",
            objective_update=ObjectiveUpdate(title=title, distance_km=km),
        )

    iso_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    session_number = re.search(r"s[ée]ance\s+(\d+)", text)
    if iso_date and session_number and any(word in text for word in ("déplace", "report")):
        return CoachResponse(
            message="Je peux déplacer cette séance. Confirme la proposition ci-dessous.",
            action="reschedule_session",
            session_index=max(0, int(session_number.group(1)) - 1),
            new_date=iso_date.group(1),
        )

    if any(word in text for word in ("difficile", "dur", "épuis", "fatigu")):
        return CoachResponse(message="Je retiens que la séance était difficile. As-tu seulement une fatigue générale, ou aussi une douleur inhabituelle ? Je tiendrai compte de ta réponse avant de proposer une hausse de charge.")

    objective = context.get("objectif") if isinstance(context, dict) else None
    title = objective.get("title") if isinstance(objective, dict) else None
    suffix = f" Ton objectif actuel est « {title} »." if title else ""
    return CoachResponse(message="J'ai bien reçu ta demande." + suffix + " Pour te répondre précisément, indique ton ressenti après la dernière séance : facile, normal ou difficile, et signale toute douleur.")


def store_response(request_id: str, response: CoachResponse) -> dict[str, Any]:
    """Enregistre une réponse validée et la rattache au message utilisateur."""
    with _LOCK:
        messages = _load_unlocked()
        request = next(
            (
                item
                for item in messages
                if item.get("id") == request_id and item.get("role") == "user"
            ),
            None,
        )
        if request is None:
            raise ValueError("Demande du coach introuvable.")
        existing = next(
            (
                item
                for item in messages
                if item.get("role") == "assistant"
                and item.get("reply_to") == request_id
            ),
            None,
        )
        if existing is not None:
            return existing
        request["status"] = "processed"
        answer = {
            "id": uuid.uuid4().hex[:12],
            "role": "assistant",
            "content": response.message,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "complete",
            "reply_to": request_id,
            "plan_modifie": response.plan_modifie,
            "action": response.action,
            "objective_update": (
                response.objective_update.model_dump(exclude_none=True)
                if response.objective_update
                else None
            ),
            "session_index": response.session_index,
            "new_date": response.new_date,
        }
        messages.append(answer)
        _save_unlocked(messages)
    return answer


def save_response(request_id: str, source: Path) -> dict[str, Any]:
    try:
        response = CoachResponse.model_validate_json(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Réponse du coach introuvable.") from exc
    return store_response(request_id, response)
