from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR, ensure_directories


USAGE_FILE = DATA_DIR / "ai_usage.json"
_LOCK = threading.Lock()


def record_usage(
    response: Any,
    category: str,
    model: str,
    *,
    duration_seconds: float | None = None,
    payload_bytes: int | None = None,
    cache_status: str | None = None,
) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    input_tokens = int(getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", 0)) or 0)
    output_tokens = int(getattr(usage, "output_tokens", getattr(usage, "completion_tokens", 0)) or 0)
    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or input_tokens + output_tokens)
    input_details = getattr(usage, "input_tokens_details", None)
    cached_input_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    effective_cache_status = "hit" if cached_input_tokens else (cache_status or "miss")
    target = Path(USAGE_FILE)
    with _LOCK:
        try:
            entries = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else []
        except (OSError, json.JSONDecodeError):
            entries = []
        entries.append({
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "category": category,
            "model": model,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "duration_seconds": duration_seconds,
            "payload_bytes": payload_bytes,
            "cache_status": effective_cache_status,
        })
        ensure_directories()
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(entries[-2000:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)


def usage_summary(files: Iterable[Path] | None = None) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for usage_file in files or [Path(USAGE_FILE)]:
        try:
            payload = json.loads(usage_file.read_text(encoding="utf-8")) if usage_file.is_file() else []
        except (OSError, json.JSONDecodeError):
            payload = []
        if isinstance(payload, list):
            entries.extend(item for item in payload if isinstance(item, dict))
    categories: dict[str, dict[str, int]] = {}
    for entry in entries:
        bucket = categories.setdefault(str(entry.get("category") or "autre"), {"calls": 0, "input": 0, "cached_input": 0, "output": 0, "total": 0})
        bucket["calls"] += 1
        bucket["input"] += int(entry.get("input_tokens") or 0)
        bucket["cached_input"] += int(entry.get("cached_input_tokens") or 0)
        bucket["output"] += int(entry.get("output_tokens") or 0)
        bucket["total"] += int(entry.get("total_tokens") or 0)
    total = sum(item["total"] for item in categories.values())
    calls = sum(item["calls"] for item in categories.values())
    average = round(total / calls) if calls else 0
    level = "Économique" if average < 2500 else "Modéré" if average < 6000 else "Élevé"
    return {"total": total, "input": sum(item["input"] for item in categories.values()), "cached_input": sum(item["cached_input"] for item in categories.values()), "output": sum(item["output"] for item in categories.values()), "calls": calls, "average": average, "level": level, "categories": categories}
