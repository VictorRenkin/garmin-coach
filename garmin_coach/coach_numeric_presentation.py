"""Small display-only companion to canonical summaries sent to the chat model."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from .summary import _duration as format_duration


def _summary_display(summary: dict[str, Any]) -> dict[str, Any]:
    seconds = summary.get("observed_duration_seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        seconds = None
    try:
        days = (date.fromisoformat(summary["end_date"]) - date.fromisoformat(summary["start_date"])).days + 1
        if days <= 0:
            days = None
    except (KeyError, TypeError, ValueError):
        days = None
    weekly = round(seconds * 7 / days, 6) if seconds is not None and days else None
    return {
        "observed_duration_seconds": seconds,
        "observed_duration_hms": format_duration(seconds) if seconds is not None else None,
        "period_days": days,
        "average_weekly_observed_duration_seconds": weekly,
        "average_weekly_observed_duration_hms": format_duration(weekly) if weekly is not None else None,
    }


def build_numeric_presentation(context: dict[str, Any]) -> dict[str, Any]:
    """Format at most six existing summaries; never read or aggregate activities.

    Weekly normalization uses the full inclusive calendar period, including days
    without recorded activity. Missing durations remain missing, even with dates.
    Canonical payloads and their confidence/import/budget metadata are untouched.
    """
    summaries = {}
    multi = context.get("contexte_multisport") or {}
    if multi.get("status") == "available":
        windows = (multi.get("history") or {}).get("windows") or {}
        for name in ("last_28_days", "last_84_days"):
            if isinstance(windows.get(name), dict):
                summaries[f"contexte_multisport.history.windows.{name}"] = _summary_display(windows[name])
        weeks = multi.get("weeks") or {}
        for name in ("last_7_days", "previous_7_days"):
            summary = (weeks.get(name) or {}).get("summary")
            if isinstance(summary, dict):
                summaries[f"contexte_multisport.weeks.{name}.summary"] = _summary_display(summary)
    search = context.get("recherche_historique") or {}
    if search.get("status") == "available":
        for index, period in enumerate((search.get("periods") or [])[:2]):
            summary = period.get("summary")
            if isinstance(summary, dict):
                summaries[f"recherche_historique.periods[{index}].summary"] = _summary_display(summary)
    return {"rounding": "nearest_second", "summaries": summaries} if summaries else {}
