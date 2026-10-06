from __future__ import annotations

from contextlib import nullcontext

from garmin_coach import garmin_setup


def _stub_import(monkeypatch, events: list[str]) -> None:
    monkeypatch.setattr(garmin_setup, "user_context", lambda user_id: nullcontext())
    monkeypatch.setattr(garmin_setup, "load_settings", lambda: object())
    monkeypatch.setattr(
        garmin_setup, "connect", lambda settings: events.append("connect") or object()
    )
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
        "build_runtime_motionest_context",
        lambda *args, **kwargs: events.append("runtime") or object(),
    )


def test_initial_setup_runs_in_order_and_stops_at_analysis_ready(monkeypatch) -> None:
    events: list[str] = []
    _stub_import(monkeypatch, events)
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
        "runtime",
        "analysis_ready",
    ]


def test_initial_setup_has_no_ai_or_plan_generation_hook(monkeypatch) -> None:
    events: list[str] = []
    _stub_import(monkeypatch, events)
    monkeypatch.setattr(
        garmin_setup,
        "update_garmin_setup",
        lambda status, **kwargs: events.append(status) or {},
    )

    garmin_setup.run_initial_setup("u_runner")

    assert not hasattr(garmin_setup, "generate_initial_plan")
    assert not hasattr(garmin_setup, "mark_plan_proposed")
    assert "analysis_ready" in events


def test_reconnecting_legacy_complete_user_keeps_legacy_completion(monkeypatch) -> None:
    events: list[str] = []
    _stub_import(monkeypatch, events)
    monkeypatch.setattr(garmin_setup, "load_onboarding", lambda: {"status": "complete"})
    monkeypatch.setattr(
        garmin_setup,
        "update_garmin_setup",
        lambda status, **kwargs: events.append(status) or {},
    )

    garmin_setup.run_initial_setup("u_legacy")

    assert events[-1] == "complete"
    assert "analysis_ready" not in events


def test_initial_setup_no_longer_exposes_the_legacy_sync_once_limit() -> None:
    assert not hasattr(garmin_setup, "sync_once")
