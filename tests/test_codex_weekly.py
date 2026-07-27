from datetime import date

from garmin_coach import codex_weekly


def test_weekly_context_waits_until_three_sessions_are_done(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(codex_weekly, "PLANS_DIR", tmp_path)
    (tmp_path / "2026-07-20.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        codex_weekly,
        "_context_for_week",
        lambda week: {"semaine_evaluee": {"adherence_calculee": "2/3"}},
    )

    result = codex_weekly.weekly_context(date(2026, 7, 23))

    assert result == {
        "due": False,
        "reason": "Le bilan attend les trois séances terminées (2/3).",
    }


def test_weekly_context_is_due_as_soon_as_three_sessions_are_done(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(codex_weekly, "PLANS_DIR", tmp_path)
    (tmp_path / "2026-07-20.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        codex_weekly,
        "_context_for_week",
        lambda week: {"semaine_evaluee": {"adherence_calculee": "3/3"}},
    )

    result = codex_weekly.weekly_context(date(2026, 7, 23))

    assert result["due"] is True
    assert result["evaluated_week"] == "2026-07-20"


def test_weekly_context_skips_when_next_plan_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(codex_weekly, "PLANS_DIR", tmp_path)
    (tmp_path / "2026-07-20.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2026-07-27.json").write_text("{}", encoding="utf-8")

    result = codex_weekly.weekly_context(date(2026, 7, 27))

    assert result["due"] is False
