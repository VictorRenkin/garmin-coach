from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from garmin_coach import (
    config,
    history_import,
    history_storage,
    local_activity_history,
    storage,
    training_cycle,
)


REFERENCE = date(2026, 9, 30)


@pytest.fixture
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    return tmp_path


def activity(activity_id: int | str, day: date, sport: str = "running", **extra):
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": sport},
        "activityName": f"Activity {activity_id}",
        "startTimeLocal": f"{day.isoformat()}T08:00:00",
        "distance": 5000,
        "movingDuration": 1800,
        **extra,
    }


def initialize(user_id: str, scope: str = "all") -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import(scope, reference_date=REFERENCE)


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("3_months", ["recent"]),
        ("2_years", ["year_old", "recent"]),
        ("all", ["three_years", "year_old", "recent"]),
    ],
)
def test_reader_honors_frozen_scope(
    isolated_data: Path, scope: str, expected: list[str]
) -> None:
    user_id = f"u_scope_reader_{scope}"
    initialize(user_id, scope)
    with config.user_context(user_id):
        history_storage.save_activity_metadata(
            activity("recent", date(2026, 9, 1))
        )
        history_storage.save_activity_metadata(
            activity("year_old", date(2025, 9, 1))
        )
        history_storage.save_activity_metadata(
            activity("three_years", date(2023, 9, 1))
        )
        loaded = local_activity_history.load_local_activity_history()

    assert [item["activityId"] for item in loaded] == expected


def test_reader_is_multisport_ordered_filterable_and_keeps_unknown_types(
    isolated_data: Path,
) -> None:
    user_id = "u_multisport_reader"
    initialize(user_id)
    with config.user_context(user_id):
        for item in (
            activity("swim", date(2026, 9, 12), "lap_swimming"),
            activity("mystery", date(2026, 9, 11), "racket_sport_x"),
            activity("run", date(2026, 9, 10), "running"),
            activity("bike", date(2026, 9, 13), "cycling"),
        ):
            history_storage.save_activity_metadata(item)
        loaded = local_activity_history.load_local_activity_history()
        filtered = local_activity_history.load_local_activity_history(
            start_date="2026-09-11", end_date="2026-09-12", sport_types=["racket_sport_x"]
        )

    assert [item["activityId"] for item in loaded] == [
        "run", "mystery", "swim", "bike"
    ]
    assert local_activity_history.activity_sport_type(loaded[1]) == "racket_sport_x"
    assert [item["activityId"] for item in filtered] == ["mystery"]


def test_details_are_loaded_only_on_explicit_request(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = "u_lazy_details"
    initialize(user_id)
    with config.user_context(user_id):
        history_storage.save_activity_metadata(
            activity("with_details", date(2026, 9, 20), "cycling")
        )
        history_storage.save_activity_metadata(
            activity("without_details", date(2026, 9, 21), "running")
        )
        (Path(config.MULTISPORT_DETAILS_DIR) / "with_details.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "activity_id": "with_details",
                    "complete": False,
                    "endpoints": {},
                }
            ),
            encoding="utf-8",
        )
        calls: list[str] = []
        real_loader = local_activity_history.load_detail_state

        def details(activity_id):
            calls.append(str(activity_id))
            return real_loader(activity_id)

        monkeypatch.setattr(local_activity_history, "load_detail_state", details)
        assert len(local_activity_history.load_local_activity_history()) == 2
        assert calls == []
        enriched = local_activity_history.load_local_activity_with_details(
            "with_details"
        )
        assert enriched is not None
        assert enriched["details"]["activity_id"] == "with_details"
        assert calls == ["with_details"]


def test_missing_details_are_explicitly_none(isolated_data: Path) -> None:
    user_id = "u_missing_details"
    initialize(user_id)
    with config.user_context(user_id):
        history_storage.save_activity_metadata(
            activity("metadata_only", date(2026, 9, 20))
        )
        enriched = local_activity_history.load_local_activity_with_details(
            "metadata_only"
        )
    assert enriched is not None
    assert enriched["details"] is None


def test_large_history_stays_bounded_for_initial_context(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = "u_large_history"
    initialize(user_id)
    with config.user_context(user_id):
        monkeypatch.setattr(
            local_activity_history,
            "iter_activity_history",
            lambda: (
                activity(f"a{index}", REFERENCE - timedelta(days=index), "cycling")
                for index in range(5000)
            ),
        )
        recent = local_activity_history.load_recent_local_activities(20)

    assert len(recent) == 20
    assert [item["activityId"] for item in recent] == [
        f"a{index}" for index in range(19, -1, -1)
    ]


def test_reader_is_strictly_isolated_between_users(isolated_data: Path) -> None:
    initialize("u_reader_alpha")
    initialize("u_reader_beta")
    with config.user_context("u_reader_alpha"):
        history_storage.save_activity_metadata(
            activity("alpha", date(2026, 9, 20))
        )
    with config.user_context("u_reader_beta"):
        history_storage.save_activity_metadata(
            activity("beta", date(2026, 9, 20))
        )
        beta = local_activity_history.load_local_activity_history()
    assert [item["activityId"] for item in beta] == ["beta"]


def test_reader_preserves_import_privacy_sanitizing(isolated_data: Path) -> None:
    user_id = "u_private_reader"
    initialize(user_id)
    with config.user_context(user_id):
        history_storage.save_activity_metadata(
            activity(
                "safe",
                date(2026, 9, 20),
                latitude=50.0,
                longitude=4.0,
                polyline="secret-route",
                email="private@example.test",
                accessToken="secret-token",
            )
        )
        serialized = json.dumps(
            local_activity_history.load_local_activity_history()
        ).lower()
    for forbidden in ("latitude", "longitude", "polyline", "email", "token", "secret-route"):
        assert forbidden not in serialized


def test_legacy_fallback_only_without_multisport_import(isolated_data: Path) -> None:
    with config.user_context("u_legacy_only"):
        storage.save_activity(activity("legacy", date(2026, 9, 20)))
        assert local_activity_history.local_history_source() == "legacy"
        loaded = local_activity_history.load_local_activity_history()
    assert [item["activityId"] for item in loaded] == ["legacy"]


def test_invalid_multisport_state_never_hides_behind_legacy(
    isolated_data: Path,
) -> None:
    with config.user_context("u_invalid_multisport"):
        storage.save_activity(activity("legacy", date(2026, 9, 20)))
        Path(config.HISTORY_IMPORT_FILE).write_text("{invalid", encoding="utf-8")
        with pytest.raises(local_activity_history.LocalActivityHistoryError):
            local_activity_history.load_local_activity_history()


def planned_session(day: str) -> training_cycle.PlannedSession:
    return training_cycle.PlannedSession(
        date=day,
        titre="Endurance facile",
        type="endurance facile",
        objectif="Construire une endurance régulière.",
        raison="Séance prudente fondée sur l'historique récent.",
        echauffement="Échauffement progressif.",
        bloc_principal="Course facile et régulière.",
        retour_au_calme="Retour au calme lent.",
        duree_estimee_minutes=30,
        distance_estimee_km=5,
        consigne_intensite="Rester en aisance respiratoire.",
        echauffement_minutes=5,
        repetitions_bloc=1,
        effort_minutes=20,
        recuperation_minutes=0,
        retour_au_calme_minutes=5,
    )


def test_initial_plan_uses_49_multisport_metadata_when_legacy_is_empty(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = "u_observed_49"
    initialize(user_id)
    captured: dict = {}
    with config.user_context(user_id):
        for index in range(49):
            sport = ("running", "cycling", "other")[index % 3]
            name = "Padel" if sport == "other" else f"Activity {index}"
            history_storage.save_activity_metadata(
                activity(
                    f"a{index}",
                    REFERENCE - timedelta(days=48 - index),
                    sport,
                    activityName=name,
                    latitude=50.0,
                    ownerFullName="Private Athlete",
                )
            )
        Path(config.PROFILE_FILE).write_text(
            json.dumps(
                {
                    "objective": {
                        "title": "Progresser",
                        "sessionsPerWeek": 1,
                        "availableDays": ["mon"],
                    }
                }
            ),
            encoding="utf-8",
        )

        def complete(_settings, **kwargs):
            captured.update(kwargs["payload"])
            required_date = kwargs["payload"]["dates_obligatoires"][0]
            return training_cycle.InitialPlan(
                synthese_historique="Historique multisport local correctement chargé.",
                logique_existante="Progression prudente fondée sur les données locales.",
                adaptation_objectif="Une séance initiale respecte le niveau observé.",
                seances=[planned_session(required_date)],
                avertissement_securite="Arrêter en cas de douleur inhabituelle.",
            )

        monkeypatch.setattr(training_cycle, "structured_completion", complete)
        monkeypatch.setattr(training_cycle, "list_messages", lambda: [])
        training_cycle.generate_initial_plan(
            training_cycle.Settings(None, None, 300, "test-key"),
            {"garmin_summary": {}},
        )

    recent = captured["historique_recent"]
    assert len(recent) == 20
    assert recent != []
    assert {item["sport"] for item in recent} == {"running", "cycling", "padel"}
    assert all(
        set(item)
        == {
            "sport",
            "date",
            "distance_km",
            "duree_secondes",
            "allure_secondes_par_km",
            "denivele_positif_m",
            "frequence_cardiaque_moyenne",
            "frequence_cardiaque_max_observee",
            "cadence_moyenne",
            "vo2max_estimee_garmin",
        }
        for item in recent
    )
