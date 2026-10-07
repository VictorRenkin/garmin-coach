from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import COACH_MEMORY_FILE, ensure_directories


SCHEMA_VERSION = 2
MAX_MEMORY_ITEMS = 120
MAX_CONTEXT_ITEMS = 12
MAX_CONTEXT_CHARS = 1_800
MemoryCategory = Literal[
    "preference", "availability", "goal", "unrecorded_activity",
    "current_signal", "constraint", "decision", "event", "other",
]
MemoryScope = Literal["durable", "current", "event"]
MemoryStatus = Literal["active", "superseded", "resolved"]
MemorySource = Literal["user_declared", "legacy_user_declared"]
MemoryOperation = Literal["add", "upsert", "resolve", "supersede"]

_LOCK = threading.RLock()
_SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|password|mot de passe|token|cookie|secret)\s*[:=]|"
    r"\b(?:sk|nvapi)-[A-Za-z0-9_-]{12,}\b"
)


def _clean_text(value: object, *, maximum: int = 300) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryItemV2(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,64}$")
    category: MemoryCategory
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    text: str = Field(min_length=3, max_length=300)
    source: MemorySource
    source_message_id: str | None = Field(default=None, max_length=64)
    scope: MemoryScope
    status: MemoryStatus = "active"
    recorded_at: str
    updated_at: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        cleaned = _clean_text(value)
        if cleaned != value or _SECRET_RE.search(cleaned):
            raise ValueError("Le fait mémoire est invalide ou contient un secret.")
        return value


class MemoryUpdate(StrictModel):
    operation: MemoryOperation
    category: MemoryCategory
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    scope: MemoryScope
    text: str | None = Field(default=None, min_length=3, max_length=300)
    source_excerpt: str = Field(min_length=2, max_length=240)
    target_id: str | None = Field(default=None, max_length=64)

    @field_validator("text", "source_excerpt")
    @classmethod
    def validate_clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean_text(value)
        if cleaned != value or _SECRET_RE.search(cleaned):
            raise ValueError("La mise à jour mémoire est invalide ou contient un secret.")
        return value

    @model_validator(mode="after")
    def validate_operation(self) -> "MemoryUpdate":
        if self.operation in {"add", "upsert"} and self.text is None:
            raise ValueError("add/upsert exige un texte explicite.")
        if self.operation == "resolve" and self.scope == "durable":
            raise ValueError("Une mémoire durable doit être supersédée, pas résolue.")
        return self


class MemoryStoreV2(StrictModel):
    schema_version: Literal[2] = SCHEMA_VERSION
    items: list[MemoryItemV2] = Field(default_factory=list, max_length=MAX_MEMORY_ITEMS)


def _legacy_item(item: dict[str, Any], index: int) -> MemoryItemV2 | None:
    fact = _clean_text(item.get("fact"))
    if len(fact) < 3 or _SECRET_RE.search(fact):
        return None
    recorded_at = str(item.get("recorded_at") or _now())
    digest = hashlib.sha256(f"{index}:{fact}:{recorded_at}".encode()).hexdigest()[:16]
    return MemoryItemV2(
        id=f"legacy_{digest}", category="other", key=f"legacy_{digest}", text=fact,
        source="legacy_user_declared", scope="durable",
        recorded_at=recorded_at, updated_at=recorded_at,
    )


def _load_store_unlocked() -> MemoryStoreV2:
    target = Path(os.fspath(COACH_MEMORY_FILE))
    if not target.is_file():
        return MemoryStoreV2()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Le store de mémoire est illisible.") from exc
    if isinstance(payload, dict):
        try:
            return MemoryStoreV2.model_validate(payload)
        except ValueError as exc:
            raise ValueError("Le store de mémoire V2 est invalide.") from exc
    if not isinstance(payload, list):
        return MemoryStoreV2()
    items: list[MemoryItemV2] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            continue
        try:
            if "text" in raw and "category" in raw:
                items.append(MemoryItemV2.model_validate(raw))
            else:
                legacy = _legacy_item(raw, index)
                if legacy:
                    items.append(legacy)
        except ValueError:
            continue
    return MemoryStoreV2(items=items[-MAX_MEMORY_ITEMS:])


def _write_store_unlocked(store: MemoryStoreV2) -> None:
    ensure_directories()
    target = Path(os.fspath(COACH_MEMORY_FILE))
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2)
    try:
        temporary.write_text(encoded, encoding="utf-8")
        MemoryStoreV2.model_validate_json(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def load_memory(*, include_inactive: bool = True) -> list[dict[str, Any]]:
    """Read V2 memory and transparently adapt legacy string records."""
    with _LOCK:
        try:
            items = _load_store_unlocked().items
        except ValueError:
            items = []
    if not include_inactive:
        items = [item for item in items if item.status == "active"]
    return [item.model_dump(mode="json") for item in items]


def _excerpt_is_grounded(excerpt: str, source_text: str) -> bool:
    return _clean_text(excerpt).casefold() in _clean_text(source_text, maximum=2_000).casefold()


def apply_memory_updates(
    updates: list[MemoryUpdate | dict[str, Any]], *, source_message_id: str,
    source_text: str, now: str | None = None,
) -> list[dict[str, Any]]:
    """Validate and atomically apply AI-proposed, user-grounded memory changes."""
    validated = [u if isinstance(u, MemoryUpdate) else MemoryUpdate.model_validate(u) for u in updates]
    for update in validated:
        if not _excerpt_is_grounded(update.source_excerpt, source_text):
            raise ValueError("La mémoire proposée n'est pas ancrée dans le message utilisateur.")
    timestamp = now or _now()
    applied: list[MemoryItemV2] = []
    with _LOCK:
        items = list(_load_store_unlocked().items)
        for update in validated:
            matches = [
                item for item in items if item.status == "active" and (
                    item.id == update.target_id if update.target_id
                    else item.category == update.category and item.key == update.key
                )
            ]
            if update.operation in {"resolve", "supersede"}:
                next_status: MemoryStatus = "resolved" if update.operation == "resolve" else "superseded"
                for match in matches:
                    replacement = match.model_copy(update={"status": next_status, "updated_at": timestamp})
                    items[items.index(match)] = replacement
                    applied.append(replacement)
                if update.operation == "resolve" or update.text is None:
                    continue
            if update.operation == "upsert":
                for match in matches:
                    replacement = match.model_copy(update={"status": "superseded", "updated_at": timestamp})
                    items[items.index(match)] = replacement
                    applied.append(replacement)
            text = _clean_text(update.text)
            duplicate = next((item for item in items if item.status == "active" and item.category == update.category and item.key == update.key and item.text.casefold() == text.casefold()), None)
            if duplicate:
                continue
            created = MemoryItemV2(
                id=f"mem_{uuid.uuid4().hex[:16]}", category=update.category,
                key=update.key, text=text, source="user_declared",
                source_message_id=source_message_id, scope=update.scope,
                recorded_at=timestamp, updated_at=timestamp,
            )
            items.append(created)
            applied.append(created)
        _write_store_unlocked(MemoryStoreV2(items=items[-MAX_MEMORY_ITEMS:]))
    return [item.model_dump(mode="json") for item in applied]


def remember_facts(facts: list[str]) -> None:
    """Compatibility writer for legacy callers."""
    timestamp = _now()
    with _LOCK:
        items = list(_load_store_unlocked().items)
        known = {item.text.casefold() for item in items if item.status == "active"}
        for raw in facts:
            fact = _clean_text(raw)
            if len(fact) < 3 or fact.casefold() in known or _SECRET_RE.search(fact):
                continue
            digest = hashlib.sha256(fact.casefold().encode()).hexdigest()[:16]
            items.append(MemoryItemV2(
                id=f"legacy_{digest}", category="other", key=f"legacy_{digest}",
                text=fact, source="legacy_user_declared", scope="durable",
                recorded_at=timestamp, updated_at=timestamp,
            ))
            known.add(fact.casefold())
        _write_store_unlocked(MemoryStoreV2(items=items[-MAX_MEMORY_ITEMS:]))


def _parsed_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.astimezone()


def select_relevant_memory(
    query: str = "", *, now: datetime | None = None,
    max_items: int = MAX_CONTEXT_ITEMS, max_chars: int = MAX_CONTEXT_CHARS,
    categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a bounded deterministic active-memory projection for one AI call."""
    current = now or datetime.now().astimezone()
    folded = query.casefold()
    safety_query = any(t in folded for t in ("douleur", "bless", "fatigu", "malade", "sommeil", "reprend"))
    availability_query = any(t in folded for t in ("disponib", "mercredi", "jeudi", "déplace", "reporte"))
    goal_query = any(t in folded for t in ("objectif", "course", "chrono", "finir"))
    candidates: list[tuple[int, datetime, MemoryItemV2]] = []
    with _LOCK:
        try:
            items = _load_store_unlocked().items
        except ValueError:
            items = []
    priorities = {"constraint": 90, "current_signal": 80, "goal": 60, "availability": 55, "unrecorded_activity": 50, "preference": 45, "decision": 40, "event": 30, "other": 10}
    for item in items:
        if item.status != "active" or (categories and item.category not in categories):
            continue
        updated = _parsed_time(item.updated_at)
        age = current - updated
        if item.scope == "current" and age > timedelta(days=45):
            continue
        if item.scope == "event" and age > timedelta(days=14):
            continue
        score = priorities[item.category]
        if safety_query and item.category in {"constraint", "current_signal"}:
            score += 60
        if availability_query and item.category == "availability":
            score += 60
        if goal_query and item.category == "goal":
            score += 60
        candidates.append((score, updated, item))
    candidates.sort(key=lambda row: (row[0], row[1], row[2].id), reverse=True)
    selected: list[dict[str, Any]] = []
    used = 0
    for _, _, item in candidates:
        compact = {"id": item.id, "category": item.category, "key": item.key, "text": item.text, "scope": item.scope, "updated_at": item.updated_at}
        size = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
        if selected and used + size > max_chars:
            continue
        selected.append(compact)
        used += size
        if len(selected) >= max_items:
            break
    return selected


def decision_memory_context(
    decision: Literal[
        "goal_assessment", "planning_recommendation", "plan_generation"
    ],
) -> list[dict[str, Any]]:
    categories = (
        {"goal", "constraint", "current_signal"}
        if decision == "goal_assessment"
        else {"constraint", "current_signal", "unrecorded_activity", "preference"}
    )
    return select_relevant_memory(
        decision,
        max_items=6 if decision == "goal_assessment" else 8,
        max_chars=1_200,
        categories=categories,
    )


def activity_trace(activities: list[dict[str, Any]]) -> dict[str, Any]:
    """Résume tout l'historique sans envoyer chaque activité au modèle."""
    today = date.today()
    valid: list[tuple[date, dict[str, Any]]] = []
    for item in activities:
        try:
            day = date.fromisoformat(str(item.get("date") or "")[:10])
        except ValueError:
            continue
        valid.append((day, item))

    def summary(rows: list[tuple[date, dict[str, Any]]]) -> dict[str, Any]:
        distances = [float(item["distanceKm"]) for _, item in rows if isinstance(item.get("distanceKm"), (int, float))]
        paces = [float(item["paceSeconds"]) for _, item in rows if isinstance(item.get("paceSeconds"), (int, float))]
        return {"courses": len(rows), "distance_totale_km": round(sum(distances), 1), "distance_moyenne_km": round(sum(distances) / len(distances), 1) if distances else None, "distance_maximale_km": round(max(distances), 1) if distances else None, "allure_moyenne_secondes_km": round(sum(paces) / len(paces)) if paces else None}

    months: dict[str, list[tuple[date, dict[str, Any]]]] = defaultdict(list)
    for row in valid:
        months[row[0].strftime("%Y-%m")].append(row)
    return {
        "ensemble_historique": summary(valid),
        "derniers_28_jours": summary([row for row in valid if row[0] >= today - timedelta(days=28)]),
        "derniers_90_jours": summary([row for row in valid if row[0] >= today - timedelta(days=90)]),
        "evolution_mensuelle": [{"mois": month, **summary(months[month])} for month in sorted(months)[-6:]],
    }
