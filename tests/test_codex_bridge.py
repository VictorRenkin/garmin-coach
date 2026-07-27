import json

from garmin_coach import codex_bridge


ACTIVITY = {
    "activityId": 42,
    "activityName": "Sortie privée",
    "startTimeLocal": "2026-07-21 18:00:00",
    "distance": 5000,
    "movingDuration": 1800,
    "elevationGain": 35,
    "startLatitude": 50.1,
    "startLongitude": 4.2,
}


def test_pending_context_is_sanitized(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(codex_bridge, "load_activities", lambda: [ACTIVITY])
    monkeypatch.setattr(codex_bridge, "load_baseline_ids", lambda: set())
    monkeypatch.setattr(
        codex_bridge, "analysis_path", lambda activity_id: tmp_path / f"{activity_id}.json"
    )
    monkeypatch.setattr(codex_bridge, "find_planned_session", lambda activity: None)

    pending = codex_bridge.pending_contexts()

    assert pending[0]["activity_id"] == "42"
    encoded = json.dumps(pending, ensure_ascii=False)
    assert "Sortie privée" not in encoded
    assert "startLatitude" not in encoded
    assert "startLongitude" not in encoded


def test_save_codex_analysis_validates_and_writes_report(tmp_path, monkeypatch) -> None:
    draft = tmp_path / "draft.json"
    draft.write_text(
        json.dumps(
            {
                "respect_objectif": "indéterminable",
                "verdict": "séance positive",
                "confiance": "moyenne",
                "titre": "Une sortie régulière",
                "synthese": "La distance et l'allure forment une sortie cohérente et utile.",
                "points_reussis": ["Distance continue de cinq kilomètres."],
                "axes_progression": ["Conserver une allure régulière."],
                "contexte_terrain": "Le dénivelé positif reste modéré sur cette distance.",
                "conseil_prochaine_seance": "Prévoir une sortie facile après une journée de récupération.",
                "limites_analyse": ["Aucune sensation personnelle n'est disponible."],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.md"
    saved = []
    monkeypatch.setattr(codex_bridge, "load_activities", lambda: [ACTIVITY])
    monkeypatch.setattr(codex_bridge, "save_analysis", lambda activity, analysis: saved.append(analysis))
    monkeypatch.setattr(codex_bridge, "report_path", lambda activity: report)

    target = codex_bridge.save_codex_analysis("42", draft)

    assert target == report
    assert saved[0].verdict == "séance positive"
    assert "Une sortie régulière" in report.read_text(encoding="utf-8")
