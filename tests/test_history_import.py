from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from garmin_coach import config, history_import


@pytest.fixture
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.delenv("MOTIONEST_HISTORY_PAGE_SIZE", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("scope", "cutoff"),
    [
        ("3_months", "2026-06-30"),
        ("6_months", "2026-03-30"),
        ("1_year", "2025-09-30"),
        ("2_years", "2024-09-30"),
        ("all", None),
    ],
)
def test_each_history_scope_has_the_expected_cutoff(
    isolated_history: Path, scope: str, cutoff: str | None
) -> None:
    with config.user_context(f"u_{scope}"):
        state = history_import.initialize_history_import(
            scope, reference_date=date(2026, 9, 30)
        )

    assert state["scope"] == scope
    assert state["reference_date"] == "2026-09-30"
    assert state["cutoff_date"] == cutoff


def test_calendar_cutoff_clamps_end_of_month(isolated_history: Path) -> None:
    with config.user_context("u_calendar"):
        state = history_import.initialize_history_import(
            "1_year", reference_date=date(2024, 2, 29)
        )
    assert state["cutoff_date"] == "2023-02-28"


def test_invalid_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="Période"):
        history_import.validate_scope("last_250")


def test_default_scope_and_page_size_are_foundation_not_total_limit(
    isolated_history: Path,
) -> None:
    with config.user_context("u_default"):
        state = history_import.initialize_history_import(
            reference_date=date(2026, 9, 30)
        )

    assert state["scope"] == "2_years"
    assert state["page_size"] == 250
    assert state["next_offset"] == 0
    assert "activity_limit" not in state
    assert "max_activities" not in state
    assert "total_limit" not in state


def test_reference_date_remains_stable_after_resume(isolated_history: Path) -> None:
    first = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    later = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
    with config.user_context("u_resume"):
        history_import.initialize_history_import("2_years", now=first)
        resumed = history_import.resume_history_import(now=later)

    assert resumed["status"] == "running"
    assert resumed["reference_date"] == "2026-09-30"
    assert resumed["cutoff_date"] == "2024-09-30"
    assert resumed["started_at"] == "2026-09-30T08:00:00Z"


def test_history_import_write_is_atomic(
    isolated_history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def observed_replace(source, destination) -> None:
        source_path, destination_path = Path(source), Path(destination)
        assert source_path.suffix == ".tmp"
        json.loads(source_path.read_text(encoding="utf-8"))
        calls.append((source_path, destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(history_import.os, "replace", observed_replace)
    with config.user_context("u_atomic"):
        history_import.initialize_history_import(
            "6_months", reference_date=date(2026, 9, 30)
        )
        target = Path(os.fspath(config.HISTORY_IMPORT_FILE))

    assert calls and calls[0][1] == target
    assert target.is_file()
    assert not list(target.parent.glob("*.tmp"))


def test_state_is_reread_from_disk_after_refresh(isolated_history: Path) -> None:
    with config.user_context("u_refresh"):
        created = history_import.initialize_history_import(
            "3_months", reference_date=date(2026, 9, 30)
        )
        reread = history_import.load_history_import()

    assert reread == created


def test_stale_running_state_becomes_interrupted_and_retryable(
    isolated_history: Path,
) -> None:
    started = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    with config.user_context("u_stale"):
        history_import.initialize_history_import("2_years", now=started)
        history_import.resume_history_import(now=started)
        state = history_import.load_history_import(
            now=started + timedelta(seconds=301), stale_after_seconds=300
        )

    assert state["status"] == "interrupted"
    assert state["retryable"] is True
    assert state["error"] == "stale_heartbeat"


def test_history_import_is_strictly_isolated_between_users(
    isolated_history: Path,
) -> None:
    with config.user_context("u_first"):
        history_import.initialize_history_import(
            "3_months", reference_date=date(2026, 9, 30)
        )
    with config.user_context("u_second"):
        second_before = history_import.load_history_import()
        history_import.initialize_history_import(
            "all", reference_date=date(2026, 9, 30)
        )
    with config.user_context("u_first"):
        first_after = history_import.load_history_import()

    assert second_before["reference_date"] is None
    assert first_after["scope"] == "3_months"
    assert first_after["cutoff_date"] == "2026-06-30"


def test_history_import_contains_no_sensitive_data(isolated_history: Path) -> None:
    with config.user_context("u_private"):
        state = history_import.initialize_history_import(
            "2_years", reference_date=date(2026, 9, 30)
        )
        serialized = Path(os.fspath(config.HISTORY_IMPORT_FILE)).read_text(
            encoding="utf-8"
        ).lower()

    assert set(state) == history_import._FIELDS
    for forbidden in ("password", "token", "cookie", "mfa", "email", "user_id"):
        assert forbidden not in serialized


def test_corrupt_state_has_safe_deterministic_result(isolated_history: Path) -> None:
    with config.user_context("u_corrupt"):
        target = Path(os.fspath(config.HISTORY_IMPORT_FILE))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{broken", encoding="utf-8")
        first = history_import.load_history_import()
        second = history_import.load_history_import()

    assert first == second
    assert first["status"] == "error"
    assert first["retryable"] is True
    assert first["error"] == "invalid_history_import_state"
