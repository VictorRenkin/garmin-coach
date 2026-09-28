from __future__ import annotations

import json
import re
import threading
import unicodedata
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

from .ai_provider import routed_openai_model, structured_completion
from .ai_usage import record_usage
from .athlete_memory import remember_facts
from .config import DATA_DIR, Settings, ensure_directories


MESSAGES_FILE = DATA_DIR / "coach_messages.json"
COACH_DRAFTS_DIR = DATA_DIR / "coach_drafts"
_LOCK = threading.Lock()


class ObjectiveUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=80)
    distance_km: float | None = Field(default=None, ge=1, le=200)
    sessions_per_week: int | None = Field(default=None, ge=1, le=14)
    race_date: str | None = None


class SessionPatch(BaseModel):
    titre: str | None = Field(default=None, min_length=3, max_length=80)
    type: Literal[
        "endurance facile", "tempo", "intervalles", "sortie longue", "récupération"
    ] | None = None
    objectif: str | None = Field(default=None, min_length=10, max_length=250)
    raison: str | None = Field(default=None, min_length=10, max_length=300)
    echauffement: str | None = Field(default=None, min_length=3, max_length=200)
    bloc_principal: str | None = Field(default=None, min_length=5, max_length=400)
    retour_au_calme: str | None = Field(default=None, min_length=3, max_length=200)
    duree_estimee_minutes: int | None = Field(default=None, ge=15, le=150)
    distance_estimee_km: float | None = Field(default=None, ge=2, le=25)
    consigne_intensite: str | None = Field(default=None, min_length=5, max_length=250)
    echauffement_minutes: int | None = Field(default=None, ge=3, le=25)
    repetitions_bloc: int | None = Field(default=None, ge=1, le=12)
    effort_minutes: int | None = Field(default=None, ge=1, le=100)
    recuperation_minutes: int | None = Field(default=None, ge=0, le=15)
    retour_au_calme_minutes: int | None = Field(default=None, ge=3, le=25)
    echauffement_zone_fc: int | None = Field(default=None, ge=1, le=5)
    echauffement_zone_fc_fin: int | None = Field(default=None, ge=1, le=5)
    bloc_zone_fc: int | None = Field(default=None, ge=1, le=5)
    recuperation_zone_fc: int | None = Field(default=None, ge=1, le=5)
    retour_au_calme_zone_fc: int | None = Field(default=None, ge=1, le=5)


class CoachResponse(BaseModel):
    message: str = Field(min_length=2, max_length=3000)
    source: Literal["ai", "openai", "local"] = "local"
    plan_modifie: bool = False
    action: Literal["none", "update_objective", "continue_existing_plan", "create_plan", "reschedule_session", "modify_session", "delete_sessions", "adapt_plan"] = "none"
    plan_adjustment: Literal["lighter", "progressive", "recovery"] | None = None
    objective_update: ObjectiveUpdate | None = None
    session_index: int | None = Field(default=None, ge=0)
    session_indexes: list[int] = Field(default_factory=list, max_length=7)
    new_date: str | None = None
    session_patch: SessionPatch | None = None
    memory_updates: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_action(self) -> "CoachResponse":
        if self.action == "update_objective" and self.objective_update is None:
            raise ValueError("Une mise à jour d'objectif est requise.")
        if self.action == "reschedule_session" and (
            self.session_index is None or self.new_date is None
        ):
            raise ValueError("La séance et sa nouvelle date sont requises.")
        if self.action == "adapt_plan" and self.plan_adjustment is None:
            raise ValueError("Le type d'adaptation du plan est requis.")
        if self.action == "modify_session" and (
            self.session_index is None or not self.session_patch
        ):
            raise ValueError("La séance et ses nouvelles consignes sont requises.")
        if self.action == "delete_sessions" and not self.session_indexes:
            raise ValueError("Au moins une séance à retirer est requise.")
        if len(set(self.session_indexes)) != len(self.session_indexes):
            raise ValueError("Une séance ne peut être retirée qu'une fois.")
        return self


COACH_CHAT_INSTRUCTIONS = """
Le champ plan_actif est la source de verite actuelle. S'il contredit un ancien
message, decris uniquement le plan_actif et explique que l'ancienne proposition
n'est pas appliquee. Une discussion ou une confirmation textuelle ne prouve
jamais un changement : seul un dernier evenement d'acceptation appliquee le fait.

Tu es le coach multisport prudent de Motionest. Réponds en français, clairement,
en moins de 180 mots. Le contexte contient l'objectif, le plan, les métriques
Garmin anonymisées, une synthèse de l'historique, la mémoire durable et les
échanges récents du même chat. N'invente aucune donnée,
douleur, fatigue ou séance.

Ne présume jamais que les zones cardiaques d'un utilisateur sont bien ou mal
calibrées. Utilise uniquement les indices présents dans son propre contexte. Si
leur fiabilité n'est pas établie, présente-les comme indicatives et ne demande
pas de marcher dans le seul but de respecter une zone. Pour la course, analyse
aussi la relation entre allure, fréquence cardiaque, relief et historique. Pour
les autres sports, adapte tes critères au sport observé sans convertir
arbitrairement leur charge en kilomètres de course. Le ressenti complète les
mesures mais ne doit jamais être inventé.

Tu peux répondre aux questions sur l'entraînement, la récupération, les objectifs,
la charge, les séances manquées et la préparation d'une course. Après une nouvelle
course ou quand le ressenti manque, pose une seule question courte et utile, par
exemple sur l'effort perçu, la fatigue ou une douleur. Réutilise la réponse dans
les échanges suivants. Tu peux aussi proposer une action structurée, qui sera
toujours confirmée dans l'application :
- update_objective si la demande contient une modification explicite du titre,
  de la distance, du nombre de séances ou de la date de course ;
- reschedule_session exclusivement pour changer le jour calendrier d'une séance,
  seulement si l'utilisateur demande un déplacement et donne une date ISO. Ne
  l'utilise jamais pour changer le type ou le contenu d'un entraînement ;
- modify_session si l'utilisateur veut remplacer ou modifier précisément une
  séance. Fournis son index et session_patch avec tous les champs à changer
  (titre, type, objectif, raison, durées, distance, bloc, intensité et zones).
  Décris la proposition dans le message mais ne prétends pas l'avoir appliquée ;
  après la confirmation explicite de l'utilisateur, retourne immédiatement
  action=modify_session avec le patch structuré. Ne demande jamais une troisième
  confirmation. Pour des intervalles, renseigne au minimum le type, le titre,
  l'objectif, la raison, les trois descriptions de phase, toutes les durées,
  les répétitions, la récupération, la distance, l'intensité et les zones ;
- adapt_plan si l'utilisateur demande explicitement d'alléger, de renforcer
  progressivement ou de transformer la semaine en récupération. Utilise
  plan_adjustment=lighter, progressive ou recovery ;
- delete_sessions si l'utilisateur demande explicitement de retirer une ou
  plusieurs séances du plan actif. Fournis leurs index exacts dans
  session_indexes. Si l'utilisateur dit de ne garder qu'une séance clairement
  identifiable, inclus tous les autres index. Le bouton demandera sa confirmation ;
- none dans tous les autres cas, notamment si une précision est nécessaire.
Ton périmètre de modification est strictement limité aux champs de l'objectif et
aux séances structurées prévues par ces actions. Tu ne peux jamais modifier la
connexion Garmin, les clés API, le fournisseur d'IA, le code, les routes, les
permissions, les règles de confirmation ni aucune autre partie de l'application.
Le contexte peut contenir un onboarding Garmin. Si son statut est needs_profile,
considère tout objectif par défaut comme un simple exemple : commence par résumer
les courses et entraînements détectés, puis recueille l'objectif, la date, le
nombre réaliste de séances hebdomadaires.
Quand ces trois informations sont connues, retourne immédiatement
action=update_objective avec tous les champs structurés. Le message doit seulement
récapituler l'objectif proposé et demander sa confirmation. Ne pose aucune autre
question dans ce message : disponibilités et contraintes appartiennent à l'étape
suivante, après confirmation. Ne redemande jamais une information déjà fournie.
Après chaque réponse de l'utilisateur, réponds concrètement à ce qu'il vient de
dire puis, seulement si une information indispensable manque, termine par une
seule question utile. Ne laisse jamais une réponse sans suite pendant
l'onboarding, sauf lorsqu'un bouton de confirmation attend encore son choix.
Évite les répétitions : ne récapitule pas les mêmes chiffres, avertissements ou
limites déjà expliqués dans les deux derniers échanges. Si l'utilisateur corrige
explicitement une information (par exemple il n'a plus mal), utilise la nouvelle
information sans répéter l'ancien avertissement, tout en restant prudent si un
nouveau symptôme est déclaré. Ne demande jamais une confirmation textuelle si
une action structurée peut afficher immédiatement son bouton de confirmation.
Ne propose aucune création tant que l'objectif et la fréquence ne sont pas
explicitement confirmés. Quand le statut devient ready et que l'utilisateur
demande la suite, examine d'abord plan_actif. S'il contient encore des séances à
venir, cite leurs titres et dates et propose de les terminer avant que le coach
prenne le relais ; utilise action=continue_existing_plan. Ne propose jamais de
les remplacer sans le demander explicitement. S'il n'existe aucune séance à
venir, utilise action=create_plan pour préparer un brouillon. Ne dis jamais que
ce bouton envoie sur Garmin : la vraie liste sera affichée et confirmée ensuite.
Ne prétends jamais qu'une action a déjà été appliquée. plan_modifie reste false.
Ne modifie jamais une séance de ta propre initiative. Si des informations
manquent, pose une question directe et attends la réponse. Explique ensuite le
changement proposé et attends une confirmation explicite avant de produire une
action structurée. Une analyse seule ne vaut jamais autorisation.
Ajoute dans memory_updates uniquement les faits durables explicitement déclarés
par l'utilisateur et utiles aux futures décisions : disponibilités, contraintes,
préférences, objectif, ressenti récurrent, douleur déclarée ou décision importante.
Chaque fait doit être autonome et bref. N'y place jamais une supposition, un
diagnostic, une donnée secrète ni une simple formule de politesse.
Quand le dernier événement indique qu'une proposition vient d'être acceptée,
confirme que tu as compris la décision, explique concrètement ce qu'elle change
et poursuis naturellement l'accompagnement. Ne repropose pas la même action et
ne redemande pas sa confirmation.
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


def mark_action_rejected(message_id: str) -> None:
    with _LOCK:
        messages = _load_unlocked()
        message = next((item for item in messages if item.get("id") == message_id), None)
        if message is None:
            raise ValueError("Réponse du coach introuvable.")
        message["action_status"] = "rejected"
        _save_unlocked(messages)


def claim_action(message_id: str) -> dict[str, Any]:
    """RÃ©serve atomiquement une action pour empÃªcher les doubles clics coÃ»teux."""
    with _LOCK:
        messages = _load_unlocked()
        message = next(
            (
                item
                for item in messages
                if item.get("id") == message_id and item.get("role") == "assistant"
            ),
            None,
        )
        if message is None or message.get("action") in {None, "none"}:
            raise ValueError("Proposition du coach introuvable.")
        if message.get("action_status"):
            raise ValueError("Cette proposition a dÃ©jÃ  reÃ§u une rÃ©ponse.")
        message["action_status"] = "applying"
        _save_unlocked(messages)
        return dict(message)


def release_action_claim(message_id: str) -> None:
    """LibÃ¨re une rÃ©servation si l'application rÃ©elle a Ã©chouÃ©."""
    with _LOCK:
        messages = _load_unlocked()
        message = next((item for item in messages if item.get("id") == message_id), None)
        if message is not None and message.get("action_status") == "applying":
            message.pop("action_status", None)
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


def submit_message(content: str, coach_mode: str = "balanced") -> dict[str, Any]:
    normalized = " ".join(content.strip().split())
    if not normalized:
        raise ValueError("Écris une demande avant de l'envoyer.")
    if len(normalized) > 2000:
        raise ValueError("La demande ne peut pas dépasser 2 000 caractères.")
    message = {
        "id": uuid.uuid4().hex[:12],
        "role": "user",
        "coach_mode": coach_mode if coach_mode in {"essential", "balanced", "deep"} else "balanced",
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


def mark_request_failed(request_id: str) -> None:
    """Marque une demande comme échouée sans inventer une réponse de coach."""
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
        if request is not None:
            request["status"] = "failed"
            _save_unlocked(messages)


def generate_response(
    request: dict[str, Any], settings: Settings, context: dict[str, Any]
) -> CoachResponse:
    mode = str(request.get("coach_mode") or "balanced")
    mode_settings = {
        "essential": (280, "Réponds de façon très concise, avec uniquement la recommandation prioritaire."),
        "balanced": (500, "Réponds clairement avec l'explication utile et les prochaines étapes."),
        "deep": (850, "Fournis une analyse approfondie, structurée et justifiée par les données disponibles."),
    }
    output_budget, mode_instruction = mode_settings.get(mode, mode_settings["balanced"])
    grounded = _grounded_current_plan_response(str(request.get("content") or ""), context)
    if grounded is not None:
        return grounded
    explicit_action = _explicit_calendar_action(str(request.get("content") or ""), context)
    if explicit_action is not None:
        return explicit_action
    """Produit la réponse affichée par le tableau de bord via l'API configurée."""
    if not settings.ai_api_key:
        raise RuntimeError(
            "Connecte ChatGPT avant d'envoyer un message : le mode de réponses "
            "automatiques est désactivé."
        )
    try:
        if settings.ai_provider == "openai" and not settings.ai_base_url:
            model = routed_openai_model(
                settings, "deep" if mode == "deep" else "economy"
            )
            client = OpenAI(api_key=settings.ai_api_key, timeout=300.0, max_retries=0)
            result = client.responses.parse(
                model=model,
                reasoning={"effort": "none"},
                instructions=COACH_CHAT_INSTRUCTIONS + "\n\n" + mode_instruction,
                input=json.dumps(
                    {"demande": request["content"], "contexte": context},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                text_format=CoachResponse,
                max_output_tokens=output_budget,
            )
            record_usage(result, "conversation", model)
            if result.output_parsed is None:
                raise RuntimeError("L'IA n'a pas produit de réponse structurée.")
            return result.output_parsed.model_copy(update={"source": "openai"})
        response = structured_completion(
            settings,
            instructions=COACH_CHAT_INSTRUCTIONS + "\n\n" + mode_instruction,
            payload={"demande": request["content"], "contexte": context},
            result_type=CoachResponse,
            reasoning_effort="none",
            category="conversation",
            max_output_tokens=output_budget,
        )
        response = _repair_invalid_training_action(
            request=request, response=response, settings=settings, context=context
        )
        return response.model_copy(update={"source": "ai"})
    except Exception as exc:
        raise RuntimeError(
            "L'assistant IA est temporairement indisponible. Réessaie dans un instant."
        ) from exc
    raise RuntimeError("L'assistant IA n'a pas renvoyé de réponse. Réessaie.")


def _explicit_calendar_action(
    content: str, context: dict[str, Any]
) -> CoachResponse | None:
    plain = "".join(
        character
        for character in unicodedata.normalize("NFKD", content.lower())
        if not unicodedata.combining(character)
    )
    iso_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", plain)
    session_number = re.search(r"seance\s+(\d+)", plain)
    if not (
        iso_date
        and session_number
        and any(word in plain for word in ("deplace", "report"))
    ):
        return None
    sessions = (context.get("plan_actif") or {}).get("seances", [])
    session_index = max(0, int(session_number.group(1)) - 1)
    if session_index >= len(sessions):
        return CoachResponse(
            message="Cette seance n'existe pas dans le plan actif.", source="ai"
        )
    session = sessions[session_index]
    return CoachResponse(
        message=(
            f"Je propose de deplacer \"{session.get('titre', 'la seance')}\" "
            f"du {session.get('date', '-')} au {iso_date.group(1)}. "
            "Accepte ou refuse cette proposition ci-dessous."
        ),
        source="ai",
        action="reschedule_session",
        session_index=session_index,
        new_date=iso_date.group(1),
    )


def _grounded_current_plan_response(
    content: str, context: dict[str, Any]
) -> CoachResponse | None:
    """Answer exact current-plan questions from disk, never from chat history."""
    plain = "".join(
        character
        for character in unicodedata.normalize("NFKD", content.lower())
        if not unicodedata.combining(character)
    )
    if not any(
        phrase in plain
        for phrase in ("plan actif", "actuellement enregistree", "seance enregistree")
    ):
        return None
    plan = context.get("plan_actif") if isinstance(context, dict) else None
    sessions = plan.get("seances", []) if isinstance(plan, dict) else []
    weekdays = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    requested_day = next((index for index, day in enumerate(weekdays) if day in plain), None)
    selected = None
    for session in sessions:
        if not isinstance(session, dict):
            continue
        try:
            weekday = date.fromisoformat(str(session.get("date"))).weekday()
        except ValueError:
            weekday = None
        if requested_day is None or weekday == requested_day:
            selected = session
            break
    if selected is None:
        return CoachResponse(
            message="Je ne trouve aucune seance correspondante dans le plan actif.",
            source="ai",
        )
    message = (
        f"La seance actuellement enregistree le {selected.get('date')} est "
        f"\"{selected.get('titre', 'Seance')}\" ({selected.get('type', 'type non precise')}).\n"
        f"Duree estimee : {selected.get('duree_estimee_minutes', '-')} min ; "
        f"distance : {selected.get('distance_estimee_km', '-')} km."
    )
    if selected.get("consigne_intensite"):
        message += f"\nConsigne : {selected['consigne_intensite']}"
    return CoachResponse(message=message, source="ai")


def _repair_invalid_training_action(
    *, request: dict[str, Any], response: CoachResponse,
    settings: Settings, context: dict[str, Any]
) -> CoachResponse:
    """Repair a model no-op reschedule used for a confirmed content change."""
    if response.action != "reschedule_session" or response.session_index is None:
        return response
    request_text = str(request.get("content") or "").lower()
    explicit_move = any(
        token in request_text
        for token in ("dÃ©place", "deplace", "reporte", "reporter", "nouvelle date")
    )
    if explicit_move:
        return response

    repaired = structured_completion(
        settings,
        instructions=(
            "La conversation confirme une modification du CONTENU d'une sÃ©ance, "
            "pas de sa date. Retourne action=modify_session avec le mÃªme "
            "session_index et un session_patch complet reprenant exactement la "
            "proposition. Pour des intervalles, renseigne titre, type, objectif, "
            "raison, les trois phases, durÃ©es, rÃ©pÃ©titions, rÃ©cupÃ©ration, distance, "
            "intensitÃ© et zones FC. Ne demande aucune confirmation et n'utilise "
            "jamais reschedule_session."
        ),
        payload={
            "demande_confirmee": request.get("content"),
            "conversation_recente": context.get("conversation_recente", []),
            "reponse_a_corriger": response.model_dump(exclude_none=True),
        },
        result_type=CoachResponse,
        reasoning_effort="none",
    )
    if repaired.action != "modify_session":
        raise ValueError("Le modÃ¨le n'a pas produit la modification confirmÃ©e.")
    return repaired


def local_response(content: str, context: dict[str, Any]) -> CoachResponse:
    """Réponse locale immédiate pour les usages courants et les pannes d'API."""
    text = " ".join(content.lower().replace("’", "'").split())
    plain = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    objective = context.get("objectif") if isinstance(context, dict) else None
    plan = context.get("plan_actif") if isinstance(context, dict) else None
    activities = context.get("courses_recentes", []) if isinstance(context, dict) else []
    conversation = context.get("conversation_recente", []) if isinstance(context, dict) else []
    previous_assistant = next(
        (
            str(item.get("content") or "").lower()
            for item in reversed(conversation)
            if isinstance(item, dict) and item.get("role") == "assistant"
        ),
        "",
    )
    sessions_in_plan = plan.get("seances", []) if isinstance(plan, dict) else []
    title = objective.get("title") if isinstance(objective, dict) else None

    if "fatigue générale" in previous_assistant and any(
        phrase in plain for phrase in ("sans douleur", "pas de douleur", "fatigue seulement")
    ):
        return CoachResponse(
            message="Merci, je retiens une fatigue générale sans douleur. Je propose d'alléger les séances restantes de 20 % pour favoriser la récupération. Confirme ci-dessous pour modifier le plan.",
            action="adapt_plan",
            plan_adjustment="lighter",
        )
    if "jambes le lendemain" in previous_assistant and any(
        phrase in plain for phrase in ("jambes bien", "bien recupere", "aucune fatigue")
    ):
        return CoachResponse(
            message="Le bon ressenti se confirme le lendemain. Je propose une progression prudente de 10 % sur les séances restantes. Confirme uniquement si tu es toujours sans douleur.",
            action="adapt_plan",
            plan_adjustment="progressive",
        )

    if any(word in plain for word in ("poitrine", "malaise", "essoufflement inhabituel")):
        return CoachResponse(message="Une douleur thoracique, un malaise ou un essoufflement inhabituel nécessite d'arrêter l'effort et de demander rapidement un avis médical adapté. Ne reprends pas la course sur la base d'un conseil du chatbot.")
    if any(word in plain for word in ("douleur", "blesse", "blessure", "genou", "cheville")):
        return CoachResponse(message="Ne cours pas malgré une douleur. Où se situe-t-elle et apparaît-elle au repos ou seulement pendant l'effort ? Si elle est importante, persistante ou inhabituelle, demande un avis professionnel.")

    if plain in {"bonjour", "salut", "hello", "bonsoir"}:
        return CoachResponse(message=f"Bonjour ! Je peux analyser ton ressenti, expliquer tes métriques et proposer un changement d'objectif ou de calendrier.{f' Ton objectif actuel est « {title} ».' if title else ''} Comment te sens-tu après ta dernière séance ?")
    if any(phrase in plain for phrase in ("que peux-tu", "tu peux faire", "comment ca marche", "aide-moi")):
        return CoachResponse(message="Je peux comparer tes courses, recueillir ton ressenti, expliquer allure et cardio, adapter prudemment la charge, modifier un objectif et proposer le report d'une séance. Les changements restent à confirmer dans le chat.")

    sessions = re.search(r"\b([1-7])\s*(?:seances?|entrainements?)", plain)
    if sessions:
        count = int(sessions.group(1))
        return CoachResponse(
            message=f"Je peux passer l'objectif à {count} séances par semaine. Confirme la proposition ci-dessous pour l'appliquer.",
            action="update_objective",
            objective_update=ObjectiveUpdate(sessions_per_week=count),
        )

    distance = re.search(r"\b(5|10|21[,.]1|42[,.]2)\s*km\b", plain)
    if distance and any(word in plain for word in ("objectif", "prepar", "course")):
        km = float(distance.group(1).replace(",", "."))
        title = "Terminer un semi-marathon" if km == 21.1 else f"Préparer un {km:g} km"
        return CoachResponse(
            message=f"Je te propose de définir {km:g} km comme nouvel objectif. La charge évoluera progressivement après confirmation.",
            action="update_objective",
            objective_update=ObjectiveUpdate(title=title, distance_km=km),
        )

    if "semi-marathon" in plain and any(word in plain for word in ("objectif", "prepar", "change")):
        return CoachResponse(message="Je te propose le semi-marathon comme nouvel objectif. Confirme ci-dessous pour l'appliquer.", action="update_objective", objective_update=ObjectiveUpdate(title="Terminer un semi-marathon", distance_km=21.1))

    iso_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", plain)
    session_number = re.search(r"seance\s+(\d+)", plain)
    if iso_date and session_number and any(word in plain for word in ("deplace", "report")):
        return CoachResponse(
            message="Je peux déplacer cette séance. Confirme la proposition ci-dessous.",
            action="reschedule_session",
            session_index=max(0, int(session_number.group(1)) - 1),
            new_date=iso_date.group(1),
        )
    if iso_date and any(word in plain for word in ("course", "objectif", "date")):
        return CoachResponse(message=f"Je peux enregistrer le {iso_date.group(1)} comme date de course. Confirme la proposition ci-dessous.", action="update_objective", objective_update=ObjectiveUpdate(race_date=iso_date.group(1)))
    if any(word in plain for word in ("deplace", "report", "je ne peux pas courir", "indisponible")):
        upcoming = [item for item in sessions_in_plan if isinstance(item, dict)]
        summary = ", ".join(f"{index + 1}: {item.get('titre', 'séance')} le {item.get('date', '—')}" for index, item in enumerate(upcoming))
        return CoachResponse(message=("Indique le numéro de la séance et la nouvelle date au format AAAA-MM-JJ, par exemple « reporte la séance 2 au 2026-08-04 »." + (f" Séances actuelles : {summary}." if summary else "")))

    if any(word in plain for word in ("difficile", "dur", "epuise", "fatigue", "jambes lourdes")):
        return CoachResponse(message="Je retiens que la séance était difficile. As-tu seulement une fatigue générale, ou aussi une douleur inhabituelle ? Je tiendrai compte de ta réponse avant de proposer une hausse de charge.")
    if any(phrase in plain for phrase in ("je me sens bien", "etait facile", "effort facile", "bonne forme")):
        return CoachResponse(message="Je retiens un effort facile et un bon ressenti. Je ne vais pas augmenter la charge sur ce seul signal : confirme aussi comment étaient les jambes le lendemain et si le cardio est resté maîtrisé.")
    if any(phrase in plain for phrase in ("etait normale", "effort normal", "ressenti normal")):
        return CoachResponse(message="Ressenti normal enregistré. C'est cohérent pour poursuivre le plan sans hausse brutale. Y avait-il une dérive du cardio ou une difficulté particulière en fin de séance ?")
    if any(phrase in plain for phrase in ("dors mal", "mal dormi", "manque de sommeil")):
        return CoachResponse(message="Le manque de sommeil est un signal de récupération à prendre au sérieux. Garde la prochaine séance facile ou reporte le travail intense. Depuis combien de nuits dors-tu mal, et ressens-tu une fatigue dans la journée ?")
    if any(word in plain for word in ("vacances", "voyage", "deplacement professionnel")):
        return CoachResponse(message="Je peux alléger ou déplacer la semaine concernée. Donne-moi les dates d'absence et précise si tu pourras courir facilement sur place.")
    if any(phrase in plain for phrase in ("rate la seance", "rate une seance", "seance ratee", "pas fait la seance")):
        return CoachResponse(message="Ce n'est pas nécessaire de rattraper en augmentant la charge. Souhaites-tu reporter cette séance, ou la marquer comme non faite et poursuivre le plan ?")
    if any(phrase in plain for phrase in ("reduis la charge", "diminue la charge", "semaine plus legere")):
        return CoachResponse(message="Je propose d'alléger les séances restantes d'environ 20 %, tout en conservant la continuité. Confirme ci-dessous pour modifier le plan.", action="adapt_plan", plan_adjustment="lighter")
    if any(phrase in plain for phrase in ("semaine de recuperation", "mode recuperation", "recuperation cette semaine")):
        return CoachResponse(message="Je propose de convertir les séances restantes en récupération facile, sans intensité haute. Confirme ci-dessous pour modifier le plan.", action="adapt_plan", plan_adjustment="recovery")
    if any(phrase in plain for phrase in ("augmente la charge", "plus de charge", "20 %")):
        return CoachResponse(message="Je propose une progression prudente d'environ 10 % sur les séances restantes. Confirme uniquement si tu es récupéré, sans douleur et régulier récemment.", action="adapt_plan", plan_adjustment="progressive")
    if "22 min/km" in plain or "22 min /km" in plain:
        return CoachResponse(message="Une pointe à 22 min/km peut correspondre à un arrêt, de la marche ou une mesure erronée. Le graphe la masque si elle est aberrante par rapport à la médiane, sans effacer la donnée Garmin. Était-ce une valeur isolée ou l'allure moyenne ?")
    if "cardio" in plain or "frequence cardiaque" in plain:
        return CoachResponse(message="Le cardio peut monter avec l'intensité, la chaleur, le relief ou une dérive progressive. Je comparerai surtout la courbe, les zones et l'altitude. La hausse était-elle progressive, brutale, ou limitée aux montées ?")
    if any(phrase in plain for phrase in ("derniere course", "derniere seance", "ma course")) and activities:
        latest = activities[-1] if isinstance(activities[-1], dict) else {}
        distance_km = latest.get("distanceKm")
        pace = latest.get("paceSeconds")
        pace_text = f", allure moyenne {int(pace)//60}:{int(pace)%60:02d}/km" if isinstance(pace, (int, float)) else ""
        return CoachResponse(message=f"Ta dernière course enregistrée fait {distance_km if distance_km is not None else '—'} km{pace_text}. Pour l'interpréter correctement, comment as-tu vécu l'effort et le terrain était-il plat ou vallonné ?")
    if "demain" in plain and sessions_in_plan:
        return CoachResponse(message="Je peux vérifier le plan, mais ton ressenti prime avant une séance proche. Te sens-tu récupéré, sans douleur et avec une fatigue normale ? Si oui, suis la séance prévue sans ajouter de charge.")
    if any(word in plain for word in ("trouve-moi", "cherche", "internet", "region")):
        return CoachResponse(message="La recherche web en direct n'est pas disponible dans le mode local. Donne-moi ta région et ta période souhaitée ; avec l'IA en ligne configurée, je pourrai t'aider à comparer une date avec ton niveau et ton plan.")
    if plain in {"merci", "merci !", "ok", "d'accord", "super"}:
        return CoachResponse(message="Avec plaisir. Je garde le fil de la discussion pour la prochaine adaptation. Dis-moi simplement comment se passe la prochaine séance.")

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
        message_text = response.message
        if response.action == "modify_session" and not response.plan_modifie:
            message_text = re.sub(
                r"\best remplac(?:Ã©e|Ã©)\b",
                "sera remplacÃ©e aprÃ¨s ton acceptation",
                message_text,
                flags=re.IGNORECASE,
            )
        answer = {
            "id": uuid.uuid4().hex[:12],
            "role": "assistant",
            "content": message_text,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "complete",
            "reply_to": request_id,
            "plan_modifie": response.plan_modifie,
            "source": response.source,
            "action": response.action,
            "objective_update": (
                response.objective_update.model_dump(exclude_none=True)
                if response.objective_update
                else None
            ),
            "session_index": response.session_index,
            "session_indexes": response.session_indexes,
            "new_date": response.new_date,
            "plan_adjustment": response.plan_adjustment,
            "session_patch": (
                response.session_patch.model_dump(exclude_none=True)
                if response.session_patch
                else None
            ),
        }
        messages.append(answer)
        _save_unlocked(messages)
        remember_facts(response.memory_updates)
    return answer


def save_response(request_id: str, source: Path) -> dict[str, Any]:
    try:
        response = CoachResponse.model_validate_json(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Réponse du coach introuvable.") from exc
    return store_response(request_id, response)
