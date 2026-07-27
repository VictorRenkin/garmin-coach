import json

import pytest
from pydantic import ValidationError

from garmin_coach.ai_analysis import (
    CourseAnalysis,
    _heart_rate_assessment,
    build_context,
    render_analysis_report,
    save_analysis,
)


ACTIVITY = {
    "activityId": 88,
    "activityName": "Home Address Running",
    "ownerFullName": "Private Runner",
    "startTimeLocal": "2026-07-22 08:00:00",
    "distance": 5000,
    "movingDuration": 1800,
    "elevationGain": 120,
    "averageHR": 150,
    "startLatitude": 50.1,
    "startLongitude": 5.1,
}


def test_ai_context_excludes_identity_name_and_gps() -> None:
    context = build_context(ACTIVITY, [ACTIVITY])
    serialized = json.dumps(context)

    assert "Private Runner" not in serialized
    assert "Home Address" not in serialized
    assert "Latitude" not in serialized
    assert "Longitude" not in serialized
    assert context["course_a_analyser"]["denivele_positif_m"] == 120
    assert context["seance_planifiee"] is None


def test_ai_report_prioritizes_verdict_and_elevation() -> None:
    analysis = CourseAnalysis(
        respect_objectif="indéterminable",
        verdict="séance positive",
        confiance="moyenne",
        titre="Sortie vallonnée cohérente",
        synthese="La distance et le dénivelé constituent un travail d'endurance cohérent.",
        points_reussis=["Distance régulière"],
        axes_progression=["Stabiliser la fréquence hebdomadaire"],
        contexte_terrain="Les 120 mètres de dénivelé rendent l'allure brute moins comparable.",
        conseil_prochaine_seance="Prévoir une sortie facile avant un nouveau travail soutenu.",
        limites_analyse=["Aucune séance planifiée n'était disponible."],
    )

    report = render_analysis_report(ACTIVITY, analysis)

    assert "Séance planifiée respectée : Indéterminable" in report
    assert "Dénivelé positif : 120 m" in report
    assert "Fréquence cardiaque moyenne : 150 bpm" in report


def test_heart_rate_assessment_compares_time_with_planned_zones() -> None:
    assessment = _heart_rate_assessment(
        {
            "heart_rate_zones": [
                {"zoneNumber": 1, "zoneLowBoundary": 100, "secsInZone": 600},
                {"zoneNumber": 2, "zoneLowBoundary": 120, "secsInZone": 1200},
                {"zoneNumber": 3, "zoneLowBoundary": 140, "secsInZone": 200},
            ]
        },
        {
            "echauffement_zone_fc": 1,
            "echauffement_zone_fc_fin": 2,
            "bloc_zone_fc": 2,
            "recuperation_zone_fc": 1,
            "retour_au_calme_zone_fc": 1,
        },
    )

    assert assessment["temps_total_avec_zone_secondes"] == 2000
    assert assessment["part_dans_ensemble_des_zones_cibles_pct"] == 90.0
    assert assessment["repartition"][1]["pourcentage"] == 60.0



def test_analysis_length_and_item_limits() -> None:
    base = {
        "respect_objectif": "indéterminable",
        "verdict": "séance positive",
        "confiance": "moyenne",
        "titre": "Sortie régulière",
        "synthese": "Données cohérentes avec une sortie régulière.",
        "points_reussis": ["Allure stable"],
        "axes_progression": ["Limiter les variations"],
        "contexte_terrain": "Le relief limite la comparaison directe.",
        "conseil_prochaine_seance": "Conserver une intensité facile et régulière.",
    }
    with pytest.raises(ValidationError):
        CourseAnalysis(**{**base, "synthese": "x" * 241})
    with pytest.raises(ValidationError):
        CourseAnalysis(**{**base, "points_reussis": ["a", "b", "c"]})

def test_existing_analysis_is_never_overwritten(tmp_path, monkeypatch) -> None:
    target = tmp_path / "88.json"
    original = '{"model_output":{"titre":"Analyse conservée"}}'
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr("garmin_coach.ai_analysis.ANALYSES_DIR", tmp_path)

    analysis = CourseAnalysis(
        respect_objectif="indéterminable",
        verdict="séance positive",
        confiance="moyenne",
        titre="Nouvelle analyse",
        synthese="Données cohérentes avec une sortie régulière.",
        points_reussis=["Allure stable"],
        axes_progression=["Limiter les variations"],
        contexte_terrain="Le relief limite la comparaison directe.",
        conseil_prochaine_seance="Conserver une intensité facile et régulière.",
    )

    assert save_analysis(ACTIVITY, analysis) == target
    assert target.read_text(encoding="utf-8") == original