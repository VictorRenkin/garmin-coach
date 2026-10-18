from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

from .ai_provider import routed_openai_model, structured_completion
from .ai_usage import record_usage
from .coach_numeric_presentation import build_numeric_presentation
from .athlete_memory import MemoryUpdate, apply_memory_updates
from .config import DATA_DIR, Settings, ensure_directories, current_user_id
from .conversation_history_search import parse_history_request, search_conversation_history, SPORT_PATTERNS, UNSUPPORTED_SPORT_PATTERN, UNSUPPORTED_SPORT_CLARIFICATION


MESSAGES_FILE = DATA_DIR / "coach_messages.json"
COACH_DRAFTS_DIR = DATA_DIR / "coach_drafts"
_LOCK = threading.Lock()

HISTORY_SEARCH_INSTRUCTIONS = """
Le champ recherche_historique contient une lecture locale ciblée des périodes demandées.
Pour ces périodes, utilise ses synthèses canoniques, jamais les fenêtres 28/84 jours
comme substitut. Cite les dates et le sport sélectionnés. Une couverture incomplete_import
ne permet pas de conclure à une absence d'entraînement. no_local_activities signifie
seulement aucune activité locale trouvée. unavailable signifie une erreur de lecture,
pas zéro activité. Les omissions de listes ne changent pas les totaux des synthèses.
Une sélection longest_observed_distance désigne la plus grande distance observée,
elle ne prouve pas qu'une séance était une sortie longue planifiée. Ne prétends pas
avoir lu des détails, des courbes ou des zones qui ne figurent pas dans les résultats.
Lorsqu'une évolution est demandée et monthly_evolution est fourni, compare les
sous-périodes mensuelles au lieu de donner seulement le total global. Un mois
sans activité locale ne prouve pas l'absence d'entraînement réel.
"""


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
    conversation_state: Literal[
        "ready", "needs_clarification", "awaiting_confirmation"
    ] = "ready"
    clarification_topic: str | None = Field(default=None, max_length=80)
    memory_updates: list[MemoryUpdate] = Field(default_factory=list, max_length=8)

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
        if self.conversation_state == "needs_clarification":
            if not self.clarification_topic or self.action != "none":
                raise ValueError(
                    "Une clarification exige un sujet et ne peut appliquer aucune action."
                )
        if self.action != "none" and self.conversation_state == "ready":
            self.conversation_state = "awaiting_confirmation"
        return self


COACH_CHAT_INSTRUCTIONS = """
Le champ plan_actif est la source de verite actuelle. S'il contredit un ancien
message, decris uniquement le plan_actif et explique que l'ancienne proposition
n'est pas appliquee. Une discussion ou une confirmation textuelle ne prouve
jamais un changement : seul un dernier evenement d'acceptation appliquee le fait.

Tu es le coach multisport conversationnel de Motionest. Réponds en français, clairement,
en moins de 180 mots. Le contexte contient l'objectif, le plan, les métriques
Garmin anonymisées, une synthèse de l'historique, une sélection de mémoire
pertinente et au maximum huit échanges récents du même chat. N'invente aucune donnée,
douleur, fatigue ou séance.

contexte_multisport est la source prioritaire des chiffres historiques. history
contient les calculs canoniques sur 28 et 84 jours, les tendances et la confiance.
weeks contient les sept derniers jours et les sept precedents, sans recouvrement,
ancres sur reference_date. Une semaine sans activite signifie seulement aucune
activite enregistree. Si activity_list_complete est false, la liste est partielle;
utilise summary pour les totaux complets et indique la limite si pertinente.
Pour les totaux hebdomadaires, cite weeks.*.summary plutot que de recalculer
depuis la liste. Les durees sont en secondes : 3600 secondes = une heure.
presentation_numerique fournit des conversions deterministes des syntheses
identifiees par leur chemin. Pour ces syntheses, reprends observed_duration_hms
et average_weekly_observed_duration_hms tels quels au lieu de convertir les
secondes mentalement. Les libelles h/min/s sont arrondis a la seconde ; les
secondes sources restent la reference. La moyenne hebdomadaire est normalisee
sur tous les jours calendaires inclus (period_days / 7), pas sur les seules
semaines actives : compare ces moyennes pour des periodes de longueurs differentes.
Une conversion null reste manquante. Une duree observee partielle ne devient
pas un volume reel complet grace au formatage ; conserve les limites de couverture.
Les distances observed_distance_by_sport_km restent propres a chaque discipline.
Ne les additionne pas dans un total multisport et ne remplace pas leurs valeurs
individuelles par une somme. Pour comparer un volume transversal, privilegie les
durees observees, en precisant que ce volume temporel n'est pas une mesure de
charge physiologique ni de fatigue. N'invente aucune charge si elle est absente.
Une tendance indisponible sur 28 jours ne contredit pas une comparaison factuelle
des deux semaines; distingue ces deux comparaisons.
courses_recentes et trace_sportive, lorsqu'elles existent, sont des vues legacy
de course et ne remplacent jamais les chiffres canoniques.
Une valeur null est absente, jamais zero ou une estimation. Distingue les mesures
enregistrees de subjective_context (declarations). Un statut unavailable ne prouve
pas l'absence d'activites. Voir plusieurs sports ne prouve pas qu'un generateur
de plans ou un format Garmin les prend en charge.

Capacités sportives actuelles de Motionest :
- Sport observé : activité importée ou pratique déclarée, sans promesse de plan.
- Sport analysable : historique multisport (notamment vélo, natation, football),
  selon les données disponibles ; conserve leurs critères propres.
- Objectif reconnu : progresser, commencer, reprendre ou préparer une course à
  pied (5 km, 10 km, semi, marathon, trail ou autre distance de course).
  Le trail est traité comme de la course, sans générateur trail dédié.
- Séance générable : le moteur exécutable existant prépare la course à pied.
  Le contrat de proposition peut représenter d'autres sports, mais cette
  représentation ne prouve aucune génération spécialisée.
- Publication Garmin possible : uniquement les séances de course compatibles,
  après validation et résultat effectif de l'envoi. Accepter un plan ne prouve
  pas sa publication. Aucun export vélo, natation, football ou triathlon complet.
Pour un objectif non pris en charge, explique cette limite, action=none pour sa
création ou sa publication ; n'enregistre pas une distance vélo ou natation comme
objectif de course. Ne propose pas non plus de l’enregistrer comme objectif
structuré : le formulaire actuel est consacré à la course à pied. Tu peux retenir
cette intention comme déclaration conversationnelle, sans prétendre qu’un objectif
produit a été enregistré. Tu peux discuter de ces sports, analyser leur historique et
leur place dans la récupération, sans prétendre créer leurs séances ou leur plan.
Une demande de triathlon ne devient pas silencieusement un objectif de course :
propose seulement, avec son accord, de travailler la partie course à pied.
Ces limites priment sur les consignes d'onboarding demandant update_objective.
Ne prétends jamais avoir créé, appliqué ou publié un plan sans preuve explicite
correspondante dans le contexte actuel.

Tu peux enquêter avant de recommander. Si une information importante manque ou
reste ambiguë, réponds naturellement, pose UNE question courte et utile,
retourne conversation_state=needs_clarification, clarification_topic avec une
clé concise, action=none et aucune recommandation arbitraire. N'utilise pas un
questionnaire fixe. Une gêne légère ou une reprise ne vaut pas automatiquement
interdiction ; demande la précision la plus discriminante puis adapte ton conseil.
Une interdiction explicite de pratiquer déclarée par l'utilisateur est une
contrainte dure tant qu'elle n'est pas résolue. Utilise alors category=constraint,
scope=current et key=medical_no_running ou medical_no_training selon la portée.
L'insistance ne la contourne pas.
Ne diagnostique jamais.

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
Dans le même appel, ajoute dans memory_updates uniquement les informations
explicitement déclarées et utiles aux échanges futurs. Chaque opération contient :
- operation add/upsert/resolve/supersede ;
- category preference/availability/goal/unrecorded_activity/current_signal/
  constraint/decision/event/other ;
- key stable en snake_case ; scope durable/current/event ;
- text factuel pour add/upsert ;
- source_excerpt copié EXACTEMENT du dernier message utilisateur ;
- target_id quand une mémoire existante précise est corrigée ou résolue.
Utilise durable pour une préférence stable, current pour un état temporaire et
event pour un fait ponctuel. Quand l'utilisateur dit que ça va mieux, résous ou
supersède l'ancien signal au lieu de garder deux vérités actives. N'y place jamais
une supposition, un diagnostic, une causalité, une donnée Garmin transformée en
déclaration, une donnée secrète ou une simple formule de politesse. Une mémoire
n'applique jamais silencieusement un objectif, une disponibilité, une fréquence
confirmée, un plan ou une publication : ces décisions gardent leur action et leur
confirmation structurées.
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


def retry_request(request_id: str) -> dict[str, Any]:
    """Requeue one failed request without duplicating the user's message."""
    with _LOCK:
        messages = _load_unlocked()
        request = next(
            (
                item for item in messages
                if item.get("id") == request_id and item.get("role") == "user"
            ),
            None,
        )
        if request is None or request.get("status") != "failed":
            raise ValueError("Cette demande ne peut pas être relancée.")
        if any(
            item.get("role") == "assistant" and item.get("reply_to") == request_id
            for item in messages
        ):
            raise ValueError("Une réponse existe déjà pour cette demande.")
        request["status"] = "pending"
        request["retry_count"] = int(request.get("retry_count") or 0) + 1
        _save_unlocked(messages)
        return dict(request)


def _has_additional_context(content: str) -> bool:
    folded = " ".join(content.casefold().split())
    return any(
        marker in folded
        for marker in (
            "au fait", "depuis hier", "depuis quelques", "mais je", "et je",
            "aussi très", "aussi tres", ";", ". ", "? ", "! ",
        )
    )


def _validate_conversation_output(
    response: CoachResponse, request: dict[str, Any], context: dict[str, Any]
) -> CoachResponse:
    content = " ".join(str(request.get("content") or "").split()).casefold()
    for update in response.memory_updates:
        if " ".join(update.source_excerpt.split()).casefold() not in content:
            raise ValueError("Une mémoire proposée n'est pas ancrée dans le message utilisateur.")
    hard_constraint_keys = {"medical_no_running", "medical_no_training"}
    active_constraints = [
        item for item in context.get("memoire_pertinente", [])
        if isinstance(item, dict) and item.get("key") in hard_constraint_keys
    ]
    active_constraints.extend(
        {"key": update.key}
        for update in response.memory_updates
        if update.key in hard_constraint_keys and update.operation in {"add", "upsert"}
    )
    training_actions = {
        "continue_existing_plan", "create_plan", "modify_session", "adapt_plan"
    }
    frequency_change = (
        response.action == "update_objective"
        and response.objective_update is not None
        and response.objective_update.sessions_per_week is not None
    )
    if active_constraints and (response.action in training_actions or frequency_change):
        raise ValueError("Une contrainte déclarée active interdit cette action d'entraînement.")
    return response


def generate_response(
    request: dict[str, Any], settings: Settings, context: dict[str, Any]
) -> CoachResponse:
    reference = (context.get("contexte_multisport") or {}).get("reference_date")
    history_text = str(request.get("content") or "")
    # Recover only a contiguous chain of our explicit historical clarifications.
    recent = context.get("conversation_recente") or []
    prior = [item for item in recent if isinstance(item, dict)]
    if prior and prior[-1].get("role") == "user" and prior[-1].get("content") == history_text:
        prior = prior[:-1]
    clarification_messages = {
        "Quelle période souhaites-tu analyser ? Précise les mois et l’année, ou les dates.",
        "Pour quel sport souhaites-tu retrouver cette sortie longue ?",
        "Quel sport souhaites-tu analyser ?",
        "De quelle année parles-tu pour cette période historique ?",
        UNSUPPORTED_SPORT_CLARIFICATION,
    }
    while len(prior) >= 2 and prior[-1].get("role") == "assistant" and prior[-1].get("content") in clarification_messages and prior[-2].get("role") == "user":
        if prior[-1].get("content") == "De quelle année parles-tu pour cette période historique ?" and not re.fullmatch(r"\s*(?:en\s+)?((?:19|20)\d{2})\s*[.!]?\s*", str(request.get("content") or "")):
            break
        earlier_text = str(prior[-2].get("content") or "")
        if prior[-1].get("content") in ("Quel sport souhaites-tu analyser ?", UNSUPPORTED_SPORT_CLARIFICATION):
            # Replace an ambiguous discipline selection, preserving only its dates/intent.
            earlier_text = ''.join(c for c in unicodedata.normalize('NFKD', earlier_text.lower()) if not unicodedata.combining(c))
            for sport_pattern, _ in SPORT_PATTERNS:
                earlier_text = re.sub(sport_pattern, '', earlier_text)
            earlier_text = re.sub(UNSUPPORTED_SPORT_PATTERN, '', earlier_text)
        history_text = earlier_text + " " + history_text
        prior = prior[:-2]
    history_query = parse_history_request(history_text, reference) if reference else {"status": "not_requested"}
    if history_query["status"] == "needs_clarification":
        return CoachResponse(message=history_query["message"], conversation_state="needs_clarification", clarification_topic="historical_period")
    if history_query["status"] == "requested":
        athlete = current_user_id()
        context = dict(context)
        context["recherche_historique"] = search_conversation_history(athlete, history_query) if athlete else {"status": "unavailable", "error_code": "missing_active_user"}
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
    explicit_action = None
    if not _has_additional_context(str(request.get("content") or "")):
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
        model_context = dict(context)
        multi = context.get("contexte_multisport") or {}
        if multi.get("status") == "available" and multi.get("history"):
            model_context.pop("courses_recentes", None)
            model_context.pop("trace_sportive", None)
        numeric = build_numeric_presentation(model_context)
        if numeric:
            model_context["presentation_numerique"] = numeric
        payload = {"demande": request["content"], "contexte": model_context}
        encoded_payload = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        started = time.perf_counter()
        if settings.ai_provider == "openai" and not settings.ai_base_url:
            model = routed_openai_model(
                settings, "deep" if mode == "deep" else "economy"
            )
            client = OpenAI(api_key=settings.ai_api_key, timeout=60.0, max_retries=0)
            result = client.responses.parse(
                model=model,
                reasoning={"effort": "none"},
                instructions=COACH_CHAT_INSTRUCTIONS + "\n\n" + HISTORY_SEARCH_INSTRUCTIONS + "\n\n" + mode_instruction,
                input=encoded_payload,
                text_format=CoachResponse,
                max_output_tokens=output_budget,
            )
            record_usage(
                result, "conversation", model,
                duration_seconds=round(time.perf_counter() - started, 6),
                payload_bytes=len(encoded_payload.encode("utf-8")),
                cache_status="miss",
            )
            if result.output_parsed is None:
                raise RuntimeError("L'IA n'a pas produit de réponse structurée.")
            response = result.output_parsed.model_copy(update={"source": "openai"})
            return _validate_conversation_output(response, request, context)
        response = structured_completion(
            settings,
            instructions=COACH_CHAT_INSTRUCTIONS + "\n\n" + HISTORY_SEARCH_INSTRUCTIONS + "\n\n" + mode_instruction,
            payload=payload,
            result_type=CoachResponse,
            reasoning_effort="none",
            category="conversation",
            max_output_tokens=output_budget,
            timeout_seconds=60.0,
            max_retries=0,
        )
        response = _repair_invalid_training_action(
            request=request, response=response, settings=settings, context=context
        )
        response = response.model_copy(update={"source": "ai"})
        return _validate_conversation_output(response, request, context)
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
        category="conversation_repair",
        timeout_seconds=60.0,
        max_retries=0,
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
            "conversation_state": response.conversation_state,
            "clarification_topic": response.clarification_topic,
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
        if response.memory_updates:
            apply_memory_updates(
                response.memory_updates,
                source_message_id=request_id,
                source_text=str(request.get("content") or ""),
            )
    return answer


def save_response(request_id: str, source: Path) -> dict[str, Any]:
    try:
        response = CoachResponse.model_validate_json(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Réponse du coach introuvable.") from exc
    return store_response(request_id, response)
