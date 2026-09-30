from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from garmin_coach import garmin_setup


def test_initial_setup_runs_in_order_and_never_publishes(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(garmin_setup, "user_context", lambda user_id: nullcontext())
    monkeypatch.setattr(garmin_setup, "load_settings", lambda: object())
    monkeypatch.setattr(garmin_setup, "connect", lambda settings: events.append("connect") or object())
    monkeypatch.setattr(
        garmin_setup,
        "paginate_activity_history",
        lambda *args, **kwargs: events.append("paginate") or {},
    )
    monkeypatch.setattr(
        garmin_setup,
        "import_multisport_details",
        lambda *args, **kwargs: events.append("details") or {},
    )
    monkeypatch.setattr(
        garmin_setup,
        "inspect_garmin_account",
        lambda *args, **kwargs: events.append("inspect") or {"garmin_summary": {}},
    )
    monkeypatch.setattr(
        garmin_setup,
        "generate_initial_plan",
        lambda *args: events.append("generate") or Path("draft.json"),
    )
    monkeypatch.setattr(
        garmin_setup,
        "mark_plan_proposed",
        lambda path: events.append("proposed") or {},
    )
    monkeypatch.setattr(
        garmin_setup,
        "update_garmin_setup",
        lambda status, **kwargs: events.append(status) or {},
    )

    garmin_setup.run_initial_setup("u_runner")

    assert events == [
        "importing",
        "connect",
        "paginate",
        "importing",
        "details",
        "analyzing",
        "inspect",
        "preparing_plan",
        "generate",
        "proposed",
        "plan_proposed",
    ]


def test_ai_failure_is_retryable_without_garmin_reauthentication(monkeypatch) -> None:
    statuses: list[tuple[str, dict]] = []
    attempts = iter([RuntimeError("IA indisponible"), Path("draft.json")])
    monkeypatch.setattr(garmin_setup, "user_context", lambda user_id: nullcontext())
    monkeypatch.setattr(garmin_setup, "load_settings", lambda: object())
    monkeypatch.setattr(garmin_setup, "connect", lambda settings: object())
    monkeypatch.setattr(garmin_setup, "paginate_activity_history", lambda *args, **kwargs: {})
    monkeypatch.setattr(garmin_setup, "import_multisport_details", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        garmin_setup,
        "inspect_garmin_account",
        lambda *args, **kwargs: {"status": "ready", "garmin_summary": {}},
    )

    def generate(*args):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(garmin_setup, "generate_initial_plan", generate)
    monkeypatch.setattr(garmin_setup, "mark_plan_proposed", lambda path: {})
    monkeypatch.setattr(
        garmin_setup,
        "update_garmin_setup",
        lambda status, **kwargs: statuses.append((status, kwargs)) or {},
    )

    garmin_setup.run_initial_setup("u_runner")
    garmin_setup.run_initial_setup("u_runner")

    error = next(details for status, details in statuses if status == "error")
    assert error["failed_step"] == "preparing_plan"
    assert error["retryable"] is True
    assert statuses[-1][0] == "plan_proposed"


def test_reconnecting_legacy_complete_user_does_not_generate_new_plan(
    monkeypatch,
) -> None:
    statuses: list[str] = []
    generated: list[bool] = []
    monkeypatch.setattr(garmin_setup, "user_context", lambda user_id: nullcontext())
    monkeypatch.setattr(garmin_setup, "load_settings", lambda: object())
    monkeypatch.setattr(garmin_setup, "load_onboarding", lambda: {"status": "complete"})
    monkeypatch.setattr(garmin_setup, "connect", lambda settings: object())
    monkeypatch.setattr(garmin_setup, "paginate_activity_history", lambda *args, **kwargs: {})
    monkeypatch.setattr(garmin_setup, "import_multisport_details", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        garmin_setup,
        "inspect_garmin_account",
        lambda *args, **kwargs: {"status": "complete", "garmin_summary": {}},
    )
    monkeypatch.setattr(
        garmin_setup,
        "generate_initial_plan",
        lambda *args: generated.append(True),
    )
    monkeypatch.setattr(
        garmin_setup,
        "update_garmin_setup",
        lambda status, **kwargs: statuses.append(status) or {},
    )

    garmin_setup.run_initial_setup("u_legacy")

    assert generated == []
    assert statuses == ["importing", "importing", "analyzing", "complete"]


def test_initial_setup_no_longer_exposes_the_legacy_sync_once_limit() -> None:
    assert not hasattr(garmin_setup, "sync_once")
