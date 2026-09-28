from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .ai_provider import structured_completion
from .activity_data import public_detail
from .config import ANALYSES_DIR, PROFILE_FILE, REPORTS_DIR, Settings, ensure_directories


class MissingOpenAIKeyError(RuntimeError):
    pass


class CourseAnalysis(BaseModel):
    respect_objectif: Literal["oui", "non", "indéterminable"]
    respect_zones_cardiaques: Literal["oui", "non", "indéterminable"] = (
        "indéterminable"
    )
    verdict: Literal["séance positive", "séance mitigée", "séance à corriger"]
    confiance: Literal["faible", "moyenne", "élevée"]
    titre: str = Field(min_length=3, max_length=70)
    synthese: str = Field(min_length=10, max_length=240)
    points_reussis: list[str] = Field(min_length=1, max_length=2)
    axes_progression: list[str] = Field(min_length=1, max_length=2)
    contexte_terrain: str = Field(min_length=10, max_length=180)
    conseil_prochaine_seance: str = Field(min_length=10, max_length=180)
    limites_analyse: list[str] = Field(default_factory=list, max_length=2)


COACH_INSTRUCTIONS = """
Tu es un coach de course à pied prudent. Tu analyses une course terminée selon
l'objectif et la fréquence réellement confirmés par le coureur, à partir de
mesures Garmin et d'un historique sportif anonymisé.

Règles obligatoires :
- Réponds en français, très brièvement, avec des faits utiles uniquement.
- Indique d'abord si la séance planifiée et les zones cardiaques attendues ont été
  respectées, puis un ou deux points réussis et un ou deux axes de progression.
- Évite introduction, répétition, encouragement générique et longue explication.
- N'invente aucune donnée, sensation, blessure, fatigue ou intention de séance.
- Ne présume jamais que la fréquence cardiaque maximale ou les zones Garmin de
  cet utilisateur sont fiables ou erronées. Utilise uniquement les éléments de
  son contexte. Si leur calibration n'est pas établie, présente les zones comme
  indicatives et ne demande pas de marcher dans le seul but de rester en zone 2.
- Analyse principalement la relation entre vitesse en km/h et fréquence
  cardiaque mesurée, puis la régularité, le relief et l'historique comparable.
  Le ressenti peut compléter l'analyse mais ne doit pas en être la base.
- Si aucune séance planifiée n'est fournie, respect_objectif doit être
  « indéterminable ». Évalue alors seulement la cohérence et la tendance.
- Commence toujours par rappeler le type et l'objectif de la séance planifiée,
  puis tranche séparément le respect de la structure, de l'intensité et de la
  régularité à partir des mesures disponibles.
- Pour une endurance facile ou fondamentale, la réussite repose d'abord sur le
  temps passé dans la zone cible facile, la maîtrise des passages en zones 3 et
  4, et la régularité de l'allure compte tenu de l'altitude. Une allure rapide
  n'est pas un critère de réussite pour ce type de séance.- Une allure plus lente n'est pas automatiquement négative : tiens compte de la
  distance, du dénivelé et du type de comparaison disponible.
- Compare autant que possible des sorties de distance et terrain similaires.
- Quand les zones Garmin personnelles, le temps dans les zones et les zones
  cibles de la séance sont disponibles, utilise-les comme preuve principale du
  respect de l'intensité. Une moyenne cardiaque seule ne suffit pas.
- Confirme que l'intensité était adaptée uniquement si la répartition mesurée
  est cohérente avec les phases prescrites. Sinon, reste prudent, appuie-toi sur
  les mesures disponibles et évite d'insister sur les données absentes. Ne pose
  aucun diagnostic.
- Quand les séries temporelles sont disponibles, étudie la dérive cardiaque,
  l'évolution de l'allure et de la cadence en tenant compte de l'altitude.
- Utilise les tours pour comparer la longueur de foulée, la cadence et l'allure,
  mais n'interprète jamais une foulée plus longue comme automatiquement meilleure.
- Distingue une variation liée au relief d'une dégradation possible de régularité
  et signale quand les données ne permettent pas de conclure.
- Distingue clairement les faits mesurés des interprétations.
- Ne recommande jamais de courir malgré une douleur et ne remplace pas un avis
  médical. Signale les limites importantes des données.
- Le conseil de prochaine séance doit rester prudent et compatible avec la
  fréquence confirmée. Ne modifie pas encore un plan complet.
""".strip()


def _number(activity: dict[str, Any], key: str) -> float | None:
    value = activity.get(key)
    return round(float(value), 2) if isinstance(value, (int, float)) else None


def _sanitized_metrics(activity: dict[str, Any]) -> dict[str, Any]:
    distance_m = _number(activity, "distance")
    duration_s = (
        _number(activity, "movingDuration")
        or _number(activity, "duration")
        or _number(activity, "elapsedDuration")
    )
    pace_seconds = None
    if distance_m and duration_s and distance_m > 0:
        pace_seconds = round(duration_s / (distance_m / 1000))
    return {
        "date": str(activity.get("startTimeLocal") or "")[:10],
        "distance_km": round(distance_m / 1000, 2) if distance_m is not None else None,
        "duree_secondes": round(duration_s) if duration_s is not None else None,
        "allure_secondes_par_km": pace_seconds,
        "denivele_positif_m": _number(activity, "elevationGain"),
        "frequence_cardiaque_moyenne": _number(activity, "averageHR"),
        "frequence_cardiaque_max_observee": _number(activity, "maxHR"),
        "cadence_moyenne": _number(
            activity, "averageRunningCadenceInStepsPerMinute"
        ),
        "vo2max_estimee_garmin": _number(activity, "vO2MaxValue"),
    }


def _heart_rate_assessment(
    details: dict[str, Any] | None,
    planned_session: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_zones = details.get("heart_rate_zones", []) if details else []
    zones: list[dict[str, Any]] = []
    total_seconds = 0
    for raw in raw_zones if isinstance(raw_zones, list) else []:
        if not isinstance(raw, dict):
            continue
        zone = raw.get("zoneNumber")
        seconds = raw.get("secsInZone")
        if not isinstance(zone, int) or not isinstance(seconds, (int, float)):
            continue
        total_seconds += max(0, round(seconds))
        zones.append(
            {
                "zone": zone,
                "minimum_bpm": raw.get("zoneLowBoundary"),
                "secondes": max(0, round(seconds)),
            }
        )
    for zone in zones:
        zone["pourcentage"] = (
            round(zone["secondes"] / total_seconds * 100, 1)
            if total_seconds
            else 0.0
        )

    targets: dict[str, int | None] = {}
    if planned_session:
        targets = {
            "echauffement_debut": planned_session.get("echauffement_zone_fc"),
            "echauffement_fin": planned_session.get("echauffement_zone_fc_fin"),
            "bloc_principal": planned_session.get("bloc_zone_fc"),
            "recuperation": planned_session.get("recuperation_zone_fc"),
            "retour_au_calme": planned_session.get("retour_au_calme_zone_fc"),
        }
    target_zone_numbers = {
        value for value in targets.values() if isinstance(value, int)
    }
    target_seconds = sum(
        zone["secondes"] for zone in zones if zone["zone"] in target_zone_numbers
    )
    return {
        "temps_total_avec_zone_secondes": total_seconds,
        "repartition": zones,
        "zones_cibles_planifiees": targets or None,
        "part_dans_ensemble_des_zones_cibles_pct": (
            round(target_seconds / total_seconds * 100, 1)
            if total_seconds and target_zone_numbers
            else None
        ),
        "limite": (
            "La répartition globale ne permet pas d'attribuer chaque zone à une phase "
            "précise; vérifier aussi la série temporelle et les tours."
        ),
    }


def _compact_details_for_ai(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """Garde les preuves sportives utiles sans envoyer les objets Garmin bruts."""
    if not details:
        return None

    sample_fields = {
        "temps_s": (
            "sumElapsedDuration",
            "sumMovingDuration",
            "sumDuration",
            "directElapsedDuration",
        ),
        "distance_m": ("sumDistance", "directDistance"),
        "vitesse_m_s": ("directSpeed", "enhancedSpeed"),
        "fc_bpm": ("directHeartRate", "heartRate"),
        "cadence": ("directRunCadence", "directDoubleCadence"),
        "altitude_m": (
            "directCorrectedElevation",
            "directElevation",
            "directUncorrectedElevation",
        ),
        "puissance_w": ("directPower",),
    }
    series: list[dict[str, float]] = []
    for raw in details.get("samples", []):
        if not isinstance(raw, dict):
            continue
        point: dict[str, float] = {}
        for label, candidates in sample_fields.items():
            value = next(
                (
                    raw.get(candidate)
                    for candidate in candidates
                    if isinstance(raw.get(candidate), (int, float))
                ),
                None,
            )
            if value is not None:
                point[label] = round(float(value), 2)
        if point:
            series.append(point)

    lap_fields = (
        "lapIndex",
        "distance",
        "movingDuration",
        "elapsedDuration",
        "averageSpeed",
        "maxSpeed",
        "elevationGain",
        "averageHR",
        "maxHR",
        "averageRunCadence",
        "maxRunCadence",
        "strideLength",
    )
    laps = [
        {key: lap.get(key) for key in lap_fields if lap.get(key) is not None}
        for lap in details.get("laps", [])
        if isinstance(lap, dict)
    ][:80]
    zones = [
        {
            key: zone.get(key)
            for key in ("zoneNumber", "zoneLowBoundary", "secsInZone")
            if zone.get(key) is not None
        }
        for zone in details.get("heart_rate_zones", [])
        if isinstance(zone, dict)
    ][:5]
    weather = details.get("weather")
    compact_weather = (
        {
            key: weather.get(key)
            for key in (
                "temp",
                "apparentTemp",
                "relativeHumidity",
                "windSpeed",
                "windGust",
            )
            if weather.get(key) is not None
        }
        if isinstance(weather, dict)
        else {}
    )
    return {
        "series_temporelle": series,
        "tours": laps,
        "zones_cardiaques": zones,
        "meteo": compact_weather or None,
    }

def build_context(
    activity: dict[str, Any],
    history: list[dict[str, Any]],
    planned_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_id = str(activity.get("activityId", ""))
    previous = [item for item in history if str(item.get("activityId", "")) != current_id]
    previous.sort(key=lambda item: str(item.get("startTimeLocal") or ""))
    details = public_detail(current_id, max_points=72)
    profile = {
        "objectif": "à définir avec le coureur",
        "distance_objectif_km": None,
        "seances_par_semaine": None,
    }
    try:
        stored = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    objective = stored.get("objective") if isinstance(stored, dict) else None
    if isinstance(objective, dict):
        profile = {
            "objectif": objective.get("title") or profile["objectif"],
            "distance_objectif_km": objective.get("distanceKm"),
            "seances_par_semaine": objective.get("sessionsPerWeek"),
            "date_objectif": objective.get("raceDate"),
        }
    return {
        "profil": profile,
        "seance_planifiee": planned_session,
        "course_a_analyser": _sanitized_metrics(activity),
        "historique_recent": [_sanitized_metrics(item) for item in previous[-8:]],
        "details_course": _compact_details_for_ai(details),
        "evaluation_frequence_cardiaque": _heart_rate_assessment(
            details, planned_session
        ),
        "note": (
            "Aucune donnée d'identité, aucun tracé GPS et aucune coordonnée ne sont "
            "inclus dans cette requête."
        ),
    }


def analyze_activity(
    activity: dict[str, Any],
    history: list[dict[str, Any]],
    settings: Settings,
    planned_session: dict[str, Any] | None = None,
) -> CourseAnalysis:
    if not settings.ai_api_key:
        raise MissingOpenAIKeyError(
            "Aucune clé API d'IA n'est configurée dans le fichier .env."
        )
    return structured_completion(
        settings,
        instructions=COACH_INSTRUCTIONS,
        payload=build_context(activity, history, planned_session),
        result_type=CourseAnalysis,
        reasoning_effort="low",
        category="analyse_activite",
        max_output_tokens=900,
        quality="economy",
    )


def analysis_path(activity_id: str | int) -> Path:
    return ANALYSES_DIR / f"{activity_id}.json"


def report_path(activity: dict[str, Any]) -> Path:
    activity_id = str(activity.get("activityId", "inconnue"))
    date = str(activity.get("startTimeLocal") or "date-inconnue")[:10]
    return REPORTS_DIR / f"{date}-{activity_id}.md"


def save_analysis(activity: dict[str, Any], analysis: CourseAnalysis) -> Path:
    ensure_directories()
    activity_id = str(activity.get("activityId", "inconnue"))
    target = analysis_path(activity_id)
    if target.is_file():
        return target
    payload = {
        "activity_id": activity_id,
        "model_output": analysis.model_dump(mode="json"),
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def render_analysis_report(activity: dict[str, Any], analysis: CourseAnalysis) -> str:
    metrics = _sanitized_metrics(activity)
    distance = metrics["distance_km"]
    elevation = metrics["denivele_positif_m"]
    average_hr = metrics["frequence_cardiaque_moyenne"]
    max_hr = metrics["frequence_cardiaque_max_observee"]
    details = public_detail(str(activity.get("activityId", "")))
    hr = _heart_rate_assessment(details, None)
    objective = analysis.respect_objectif.capitalize()
    lines = [
        f"# Analyse IA — {analysis.titre}",
        "",
        f"**Verdict : {analysis.verdict.capitalize()}**",
        f"**Séance planifiée respectée : {objective}**",
        (
            "**Zones cardiaques attendues respectées : "
            f"{analysis.respect_zones_cardiaques.capitalize()}**"
        ),
        f"**Confiance : {analysis.confiance}**",
        "",
        analysis.synthese,
        "",
        "## Ce qui a été réussi",
        "",
        *[f"- {item}" for item in analysis.points_reussis],
        "",
        "## Axes de progression",
        "",
        *[f"- {item}" for item in analysis.axes_progression],
        "",
        "## Terrain et effort",
        "",
        analysis.contexte_terrain,
        "",
        "## Prochaine séance",
        "",
        analysis.conseil_prochaine_seance,
        "",
        "## Données utilisées",
        "",
        f"- Distance : {distance:.2f} km" if distance is not None else "- Distance : inconnue",
        f"- Dénivelé positif : {elevation:.0f} m" if elevation is not None else "- Dénivelé : inconnu",
        f"- Fréquence cardiaque moyenne : {average_hr:.0f} bpm" if average_hr is not None else "- Fréquence cardiaque moyenne : inconnue",
        f"- Fréquence cardiaque maximale observée : {max_hr:.0f} bpm" if max_hr is not None else "- Fréquence cardiaque maximale : inconnue",
    ]
    if hr["repartition"]:
        zone_summary = ", ".join(
            f"Z{zone['zone']} {zone['pourcentage']:.1f}%"
            for zone in hr["repartition"]
        )
        lines.append(f"- Temps dans les zones : {zone_summary}")
    if analysis.limites_analyse:
        lines.extend(
            ["", "## Limites", "", *[f"- {item}" for item in analysis.limites_analyse]]
        )
    lines.extend(
        [
            "",
            "> Analyse sportive informative, sans diagnostic médical. En cas de douleur, interromps l'effort et consulte un professionnel adapté.",
            "",
        ]
    )
    return "\n".join(lines)


def analyze_and_save(
    activity: dict[str, Any],
    history: list[dict[str, Any]],
    settings: Settings,
    planned_session: dict[str, Any] | None = None,
) -> Path:
    existing = analysis_path(str(activity.get("activityId", "inconnue")))
    if existing.is_file():
        return report_path(activity)
    analysis = analyze_activity(activity, history, settings, planned_session)
    save_analysis(activity, analysis)
    target = report_path(activity)
    target.write_text(render_analysis_report(activity, analysis), encoding="utf-8")
    return target


def load_analysis(activity_id: str | int) -> dict[str, Any] | None:
    target = analysis_path(activity_id)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    output = payload.get("model_output") if isinstance(payload, dict) else None
    return output if isinstance(output, dict) else None
