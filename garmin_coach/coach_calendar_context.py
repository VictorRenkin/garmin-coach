"""Bounded, read-only V2 calendar facts shared with the conversational coach."""
from __future__ import annotations

from datetime import date, datetime
import json
import re
import unicodedata

from .config import user_context
from .garmin_delivery import get_garmin_delivery_state
from .plan_acceptance import get_plan_acceptance_state
from .plan_proposal import (
    _safe_text, get_plan_proposal_state, local_calendar_blocked_dates, proposal_fingerprint,
)

MAX_CONTEXT_CHARS = 16_000
MAX_BLOCKS = 8
_DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def build_coach_calendar_context(user_id: str, *, max_chars: int = MAX_CONTEXT_CHARS) -> dict:
    """Read canonical stores only; never publish, infer completion or expose errors."""
    if max_chars < 2048:
        raise ValueError("Le budget calendrier doit être au moins 2048 caractères.")
    result = {"status": "unavailable", "source": "plan_proposal_v2",
              "source_status": "unavailable", "sessions": [], "blocked_dates": [],
              "truncated": False, "omitted_sessions": 0}
    with user_context(user_id):
        try:
            state = get_plan_proposal_state(user_id)
        except Exception:
            return result
        result["source_status"] = state.status
        if state.status != "available" or state.proposal is None:
            return result
        proposal = state.proposal
        result.update(status="available", period=proposal.period.model_dump(mode="json"),
                      proposal_fingerprint=proposal_fingerprint(proposal))
        try:
            result["blocked_dates"] = [day.isoformat() for day in
                local_calendar_blocked_dates(proposal.period.start, proposal.period.end)][:31]
            result["blocked_dates_status"] = "available"
        except Exception:
            result["blocked_dates_status"] = "unavailable"
        try:
            delivery = get_garmin_delivery_state(user_id)
            delivery_current = delivery.get("proposal_fingerprint") == result["proposal_fingerprint"]
        except Exception:
            delivery, delivery_current = {}, False
        for index, session in enumerate(proposal.sessions[:7]):
            structure = session.structure.model_dump(mode="json", exclude_none=True)
            if any(len(text) > 300 for text in (session.intensity_instruction, session.explanation, session.structure.main_set)):
                result["truncated"] = True
            structure["main_set"] = _safe_text(structure.get("main_set"))
            blocks = structure.get("blocks", [])
            for block in blocks:
                block["intensity_instruction"] = _safe_text(block.get("intensity_instruction"))
            if len(blocks) > MAX_BLOCKS:
                structure["blocks"] = blocks[:MAX_BLOCKS]
                structure["omitted_blocks"] = len(blocks) - MAX_BLOCKS
                result["truncated"] = True
            item = {"session_id": session.session_id, "date": session.date.isoformat(),
                    "starts_at": session.starts_at.isoformat() if session.starts_at else None,
                    "sport": session.sport, "session_type": session.session_type,
                    "objective": _safe_text(session.objective), "duration_minutes": session.duration_minutes,
                    "distance_km": session.distance_km,
                    "intensity_instruction": _safe_text(session.intensity_instruction),
                    "explanation": _safe_text(session.explanation), "structure": structure,
                    "provenance": "plan_proposal_v2", "activity_completed": "not_established"}
            try:
                acceptance = get_plan_acceptance_state(user_id, proposal_state=state,
                                                      session_id=session.session_id)
                item["acceptance"] = {"status": acceptance.status}
            except Exception:
                item["acceptance"] = {"status": "unavailable"}
            candidates = delivery.get("sessions", []) if delivery_current else []
            fact = next((value for value in candidates if session.session_id is not None
                         and value.get("session_id") == session.session_id), None)
            if session.session_id is None and index < len(candidates):
                fact = candidates[index]
            item["delivery"] = {"status": "available" if fact else "unavailable"}
            if fact:
                for key, allowed in {
                    "garmin_connect_status": {"created", "unknown", "not_created"},
                    "upload_status": {"confirmed", "uncertain", "not_requested"},
                    "calendar_status": {"scheduled", "not_scheduled", "verification_pending"},
                    "watch_sync_status": {"unverified"},
                }.items():
                    if fact.get(key) in allowed:
                        item["delivery"][key] = fact[key]
            result["sessions"].append(item)
    # Bound the extra projection independently of historical-context budgets.
    size = lambda: len(json.dumps(result, ensure_ascii=False))
    if size() > max_chars:
        for item in result["sessions"]:
            item.pop("structure", None)
            item["structure_truncated"] = True
        result["truncated"] = True
    while size() > max_chars and result["sessions"]:
        result["sessions"].pop()
        result["omitted_sessions"] += 1
        result["truncated"] = True
    return result


def grounded_v2_calendar_response(content: str, context: dict) -> str | None:
    """Answer a calendar read request from current V2 facts, never chat guesses."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", content.lower())
                    if not unicodedata.combining(c))
    if not any(word in plain for word in ("seance", "entrainement", "plan actif", "calendrier")):
        return None
    if any(word in plain for word in ("raccourci", "deplace", "supprim", "retire", "annul", "modifi", "rends", "change", "confirme", "accepte", "refuse")):
        return None
    calendar = context.get("calendrier_plan_v2")
    if not isinstance(calendar, dict):
        return None  # Preserve the historical reader when V2 is not projected.
    if calendar.get("status") != "available":
        if calendar.get("source_status") == "missing":
            return None  # A legacy active plan may still exist.
        return "La source des séances V2 est indisponible ou obsolète ; je ne peux pas confirmer son calendrier actuel."
    sessions = [s for s in calendar.get("sessions", []) if isinstance(s, dict)]
    weekday = next((i for i, day in enumerate(_DAYS) if re.search(rf"\b{day}\b", plain)), None)
    numeric_date = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", plain)
    iso_date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", plain)
    hour = re.search(r"\b(\d{1,2})\s*h(?:\s*(\d{2}))?\b", plain)
    period = "morning" if "matin" in plain else "evening" if "soir" in plain else None
    if "matin" in plain and "soir" in plain:
        return "Précise la séance du matin ou celle du soir pour éviter une mauvaise attribution."
    selected = []
    uncertain_time = False
    for session in sessions:
        try:
            day = date.fromisoformat(session["date"])
            starts = datetime.fromisoformat(session["starts_at"]) if session.get("starts_at") else None
        except (ValueError, KeyError, TypeError):
            continue
        if weekday is not None and day.weekday() != weekday:
            continue
        if iso_date and day.isoformat() != iso_date.group():
            continue
        if numeric_date and (day.day != int(numeric_date[1]) or day.month != int(numeric_date[2])
                             or (numeric_date[3] and day.year != int(numeric_date[3]))):
            continue
        if period or hour:
            if starts is None:
                uncertain_time = True
                continue
            if period == "morning" and starts.hour >= 12:
                continue
            if period == "evening" and starts.hour < 12:
                continue
            if hour and (starts.hour, starts.minute) != (int(hour[1]), int(hour[2] or 0)):
                continue
        selected.append(session)
    if uncertain_time:
        return "L'horaire de certaines séances n'est pas renseigné. Précise la date ou l'identité de la séance ; je ne peux pas attribuer matin ou soir."
    if not selected:
        suffix = " Le contexte est tronqué : cela ne prouve pas son absence du plan complet." if calendar.get("truncated") else ""
        return "Je ne peux pas identifier de séance V2 correspondante dans le calendrier disponible." + suffix
    focused = weekday is not None or numeric_date is not None or iso_date is not None or period is not None or hour is not None
    if focused and len(selected) > 1:
        choices = ", ".join(f"{s['date']} à {s.get('starts_at') or 'horaire non renseigné'} ({s.get('session_id') or 'identité non renseignée'})" for s in selected)
        return f"Plusieurs séances correspondent : {choices}. Laquelle souhaites-tu consulter ?"
    lines = []
    for session in selected:
        when = session["date"]
        if session.get("starts_at"):
            when += " à " + datetime.fromisoformat(session["starts_at"]).strftime("%H h %M")
        line = f"Séance prévue enregistrée dans Motionest le {when} : {session.get('duration_minutes')} min, sport {session.get('sport')}."
        if session.get("objective"):
            line += f" Objectif : {session['objective']}."
        if session.get("intensity_instruction"):
            line += f" Intensité : {session['intensity_instruction']}."
        if session.get("distance_km") is not None:
            line += f" Distance prévue : {session['distance_km']} km."
        line += f" Accord Motionest : {session.get('acceptance', {}).get('status', 'unavailable')}."
        delivery = session.get("delivery", {})
        if delivery.get("calendar_status") == "scheduled":
            line += " Programmation Garmin confirmée ; réception sur montre non vérifiée."
        elif delivery.get("upload_status") == "uncertain":
            line += " Création Garmin incertaine ; programmation non confirmée."
        elif delivery.get("garmin_connect_status") == "created":
            line += " Création Garmin confirmée, programmation non confirmée."
        else:
            line += " Programmation Garmin non confirmée."
        lines.append(line)
    lines.append("Il s'agit de séances prévues, pas d'activités déjà réalisées.")
    if calendar.get("truncated"):
        lines.append("Contexte partiel : certains détails ou séances ont été omis.")
    return "\n".join(lines)
