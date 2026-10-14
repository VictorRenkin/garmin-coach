"""Reproducible synthetic evaluation in an isolated local athlete workspace.

No Garmin connection, publication, personal data, or AI request is made here.
Run with the project Python: scripts/evaluate_motionest.py --output <directory>.
Add --serve to inspect the synthetic accounts at http://127.0.0.1:8778.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
REFERENCE = date(2026, 10, 12)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SOURCE = "https://doi.org/10.1186/s40798-022-00438-7"

PROFILES = [
    dict(id="pro", name="Alex Martin", role="Marathonien professionnel fictif", sports=["running"]*11+["strength_training"], weeks=12, requested=12, intent="race", event="marathon", target="02:12:00", concern="Preserver deux seances par jour, periodisation, repetitions et recuperations precises ; ne pas remplacer le programme du coach."),
    dict(id="football", name="Samir Laurent", role="Reprise du football apres dix mois", sports=["soccer", "running", "strength_training"], weeks=4, requested=3, intent="comeback", concern="Reconnaitre football, changements de direction et entrainements collectifs ; ne pas tout convertir en course continue."),
    dict(id="triathlon", name="Lea Moreau", role="Preparation triathlon distance olympique", sports=["lap_swimming", "cycling", "running", "strength_training", "lap_swimming", "cycling", "running", "cycling"], weeks=12, requested=8, intent="race", event="triathlon", target="02:30:00", concern="Conserver trois disciplines, transitions, blocs enchaines, watts velo et allure natation. Signaler explicitement si le produit ne gere que la course."),
    dict(id="beginner", name="Emma Bernard", role="Debutante avec objectif 5 km", sports=["running", "walking", "running"], weeks=3, requested=3, intent="beginner", concern="Consignes simples, alternance marche-course, premier benefice rapide sans jargon."),
    dict(id="comeback", name="Hugo Simon", role="Reprise de course apres six mois", sports=["running", "walking", "running"], weeks=4, requested=3, intent="comeback", gap=True, concern="Ne pas reprendre automatiquement les anciennes allures ; montrer les interruptions et la confiance."),
    dict(id="trail", name="Julie Petit", role="Traileuse avec course montagne", sports=["trail_running", "hiking", "strength_training", "running", "trail_running"], weeks=10, requested=5, intent="race", event="trail", target=None, concern="Denivele et temps d'effort comptent ; ne pas comparer toute allure a la route."),
    dict(id="busy", name="Thomas Leroy", role="Coureur avec deux creneaux et horaires variables", sports=["running", "running"], weeks=10, requested=2, intent="progress", concern="Plan court et report explicable ; aucune seance ajoutee un jour indisponible."),
    dict(id="privacy", name="Nora Dubois", role="Sportive prudente sans cardio", sports=["running", "cycling", "running"], weeks=8, requested=3, intent="progress", no_hr=True, concern="Comprendre ou vont les donnees ; ne pas inventer des zones cardio lorsque le capteur manque."),
    dict(id="pain", name="Lucas Roux", role="Coureur declarant une gene affectant son entrainement", sports=["running", "running", "strength_training"], weeks=8, requested=3, intent="comeback", pain=True, concern="Tenir compte de la declaration sans diagnostic, sans effacer la gene et sans prescrire automatiquement de l'intensite."),
    dict(id="empty", name="Ines Robert", role="Premiere utilisation sans montre ni historique", sports=[], weeks=0, requested=2, intent="beginner", concern="Distinguer ce qui est accessible avant Garmin et ce qui exige des donnees ; proposer un exemple explicitement fictif."),
    dict(id="sparse", name="Marc Fontaine", role="Historique tres court et champs manquants", sports=["running", "cycling"], weeks=1, requested=2, intent="progress", sparse=True, concern="Distinguer donnees absentes, zero et estimation ; afficher une confiance reduite."),
    dict(id="stale", name="Sarah Michel", role="Historique complet mais ancien", sports=["running", "cycling", "running"], weeks=8, requested=3, intent="comeback", stale=True, concern="Ne pas presenter des performances vieilles de huit mois comme niveau actuel."),
    dict(id="coach", name="David Henry", role="Coureur avec coach humain et plan existant", sports=["running"]*5+["strength_training"], weeks=10, requested=6, intent="race", event="half_marathon", target="01:28:00", concern="Importer ou conserver le plan existant ; demander validation avant de modifier ou publier."),
    dict(id="overload", name="Manon Olivier", role="Charge recente en forte hausse et sommeil declare mediocre", sports=["running", "cycling", "running", "strength_training"], weeks=10, requested=4, intent="progress", overload=True, concern="Expliquer hausse de volume et limites des donnees de recuperation ; ne pas assimiler declaration et mesure."),
]


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def workout(sport: str, elite: bool = False) -> dict:
    if sport in {"running", "trail_running"}:
        return dict(synthetic=True, sport=sport, title="Intervalles documentes, exemple adapte" if elite else "Seance de course fictive", steps=[
            dict(type="warmup", duration_seconds=900, target=dict(kind="effort", label="facile")),
            dict(type="repeat", repetitions=15 if elite else 4, steps=[
                dict(type="work", distance_m=1000 if elite else 400, target=dict(kind="pace", seconds_per_km_min=170 if elite else 330, seconds_per_km_max=175 if elite else 360)),
                dict(type="recovery", duration_seconds=90 if elite else 120, target=dict(kind="effort", label="facile"))]),
            dict(type="cooldown", duration_seconds=600, target=dict(kind="effort", label="facile"))],
            public_structure_source=SOURCE if elite else None,
            provenance="Synthetic scenario. Elite interval structure adapted from published literature; HR, dates and completed activities are invented. Not a prescription.")
    if sport == "cycling":
        return dict(synthetic=True, sport=sport, title="Velo avec blocs de puissance fictifs", steps=[dict(type="warmup",duration_seconds=900),dict(type="repeat",repetitions=3,steps=[dict(type="work",duration_seconds=600,target=dict(kind="power_watts",minimum=220,maximum=240)),dict(type="recovery",duration_seconds=300)]),dict(type="cooldown",duration_seconds=600)])
    if sport == "lap_swimming":
        return dict(synthetic=True, sport=sport, title="Natation technique fictive", pool_length_m=25, steps=[dict(type="warmup",distance_m=300),dict(type="repeat",repetitions=8,steps=[dict(type="work",distance_m=100,target=dict(kind="pace_per_100m",seconds=110)),dict(type="recovery",duration_seconds=20)]),dict(type="cooldown",distance_m=200)])
    if sport == "soccer":
        return dict(synthetic=True,sport=sport,title="Reprise football fictive",steps=[dict(type="warmup",duration_seconds=600),dict(type="technical_drills",duration_seconds=900),dict(type="small_sided_game",duration_seconds=1200),dict(type="cooldown",duration_seconds=300)],note="Example to test representation, not a return-to-play prescription.")
    return dict(synthetic=True,sport=sport,title="Seance complementaire fictive",steps=[dict(type="work",duration_seconds=1800)])


def generate(root: Path) -> list[dict]:
    rng = random.Random(12102026)
    result = []
    for profile in PROFILES:
        p = dict(profile)
        activities = []
        base = REFERENCE - timedelta(days=240 if p.get("stale") else 1)
        for week in range(p["weeks"]):
            for slot, sport in enumerate(p["sports"]):
                day = base - timedelta(days=week*7+(slot*7//max(1,len(p["sports"]))))
                hour = 7 if slot % 2 == 0 else 17
                elite = p["id"]=="pro"
                if sport in {"running", "trail_running"}:
                    distance = rng.choice([16000,18000,22000,30000]) if elite else rng.choice([3000,4500,6000,8000,10000])
                    pace = rng.randint(210,255) if elite else rng.randint(315,440)
                    duration = round(distance/1000*pace)
                elif sport=="cycling": distance,duration=rng.choice([25000,40000,60000]),rng.choice([3600,5400,7200])
                elif sport=="lap_swimming": distance,duration=rng.choice([1500,2000,2500]),rng.choice([2400,3000,3600])
                elif sport=="soccer": distance,duration=6500,5400
                else: distance,duration=0,1800
                if p.get("overload") and week<2: distance,duration=distance*2,duration*2
                a=dict(activityId=f"syn-{p['id']}-{week:02d}-{slot:02d}",synthetic=True,activityName=f"FICTIF {p['name']} {sport}",activityType=dict(typeKey=sport),startTimeLocal=f"{day.isoformat()}T{hour:02d}:00:00",distance=distance,duration=duration,movingDuration=duration,averageHR=rng.randint(125,160),maxHR=175,elevationGain=600 if sport=="trail_running" else 25,activityTrainingLoad=rng.randint(25,120),aerobicTrainingEffect=2.8,anaerobicTrainingEffect=0.4,hrTimeInZone_1=round(duration*.2),hrTimeInZone_2=round(duration*.65),hrTimeInZone_3=round(duration*.15))
                if sport=="cycling":a.update(averagePower=230,normalizedPower=240,averageBikeCadence=85)
                if sport=="lap_swimming":a.update(poolLength=25,numberOfActiveLengths=int(distance/25),averageSwimCadence=28)
                if p.get("no_hr") or p.get("sparse"):
                    for key in list(a):
                        if key.startswith("hrTime") or key in {"averageHR","maxHR","activityTrainingLoad","aerobicTrainingEffect","anaerobicTrainingEffect"}:a.pop(key)
                if p.get("sparse") and slot==1:a.pop("distance");a.pop("duration");a.pop("movingDuration")
                activities.append(a)
        if p.get("gap"):
            activities.append(dict(activityId="syn-comeback-old",synthetic=True,activityType=dict(typeKey="running"),activityName="Ancienne performance fictive",startTimeLocal="2026-02-01T08:00:00",distance=10000,movingDuration=2400))
        p.update(synthetic=True,activities=activities,workouts=[workout(s,p["id"]=="pro") for s in sorted(set(p["sports"]))],availability_days=DAYS if p["requested"]>=7 else DAYS[:p["requested"]])
        write(root/"datasets"/f"{p['id']}.json",p)
        result.append(p)
    write(root/"profiles.json",result)
    write(root/"manifest.json",dict(synthetic=True,reference_date=str(REFERENCE),seed=12102026,profiles=len(result),activities=sum(len(p["activities"]) for p in result),real_garmin_import=False,workout_structure_reference=SOURCE))
    return result


def isolate(root: Path):
    from garmin_coach import config,user_accounts
    root=root.resolve()
    config.GLOBAL_DATA_DIR=root/"sandbox"
    config.USERS_DATA_DIR=config.GLOBAL_DATA_DIR/"users"
    config.SECRETS_DIR=root/"sandbox-secrets"
    user_accounts.USERS_FILE=config.GLOBAL_DATA_DIR/"users.json"
    user_accounts.SESSION_KEY_FILE=config.SECRETS_DIR/"web_session.key"
    os.environ["MOTIONEST_ENV"]="development"
    os.environ["MOTIONEST_LOCAL_AUTH_BYPASS"]="true"
    return config,user_accounts


def import_and_measure(root: Path, profiles: list[dict]) -> list[dict]:
    config,accounts=isolate(root)
    from garmin_coach import history_import,history_storage,storage,onboarding
    from garmin_coach.goal import build_goal_v1
    from garmin_coach.availability import build_availability_v1
    from garmin_coach.subjective_context import build_subjective_context_v1
    from garmin_coach.runtime_context import update_motionest_inputs_v1,build_runtime_motionest_context
    results=[]
    synthetic_accounts=[]
    existing_path=root/"synthetic-accounts.json"
    existing={x["profile_id"]:x for x in json.loads(existing_path.read_text(encoding="utf-8"))} if existing_path.exists() else {}
    for p in profiles:
        if p["id"] in existing:
            uid=existing[p["id"]]["user_id"]
        else:
            account=accounts.register_account(f"{p['id']}@motionest.test","Motionest-test-2026!",terms_accepted=True)
            accounts.confirm_email(account["verificationToken"])
            uid=account["id"]
        synthetic_accounts.append(dict(profile_id=p["id"],user_id=uid,email=f"{p['id']}@motionest.test",synthetic=True))
        row=dict(profile_id=p["id"],name=p["name"],role=p["role"],user_id=uid,expected_activities=len(p["activities"]),checks=[])
        def check(name,ok,details=None):row["checks"].append(dict(check=name,passed=bool(ok),details=details))
        with config.user_context(uid):
            history_import.initialize_history_import("all",reference_date=REFERENCE)
            for a in p["activities"]:
                history_storage.save_activity_metadata(a)
                if a["activityType"]["typeKey"] in {"running","trail_running"}:storage.save_activity(a)
            count=sum(history_storage.save_activity_metadata(a)[1] for a in p["activities"])
            check("reimport_does_not_duplicate",count==0)
            history_import.update_history_import(dict(status="complete",pagination_complete=True,metadata_saved=len(p["activities"])))
        try:
            goal=build_goal_v1(p["intent"],**(dict(event_type=p["event"],distance_km=30 if p["event"]=="trail" else None,race_date="2027-04-18",target_time=p.get("target")) if p["intent"]=="race" else {}))
            row["requested_goal_accepted"]=True
        except ValueError as e:
            row["requested_goal_accepted"]=False;row["goal_error"]=str(e)
            goal=build_goal_v1("progress")
        try:
            availability=build_availability_v1(p["availability_days"],preferred_sessions_per_week=p["requested"])
            row["requested_frequency_accepted"]=True
        except ValueError as e:
            row["requested_frequency_accepted"]=False;row["frequency_error"]=str(e)
            availability=build_availability_v1(p["availability_days"],preferred_sessions_per_week=min(p["requested"],7))
        # Fallback declarations are explicit TEST fixtures, not acceptance of the requested sport.
        subjective=build_subjective_context_v1(affecting_training=bool(p.get("pain")),pain_description="Gene fictive affectant la course" if p.get("pain") else None,important_preferences=[p["concern"]])
        update_motionest_inputs_v1(uid,goal=goal,availability=availability,subjective_context=subjective)
        with config.user_context(uid):
            onboarding.onboarding_v2_state(inputs_source="v1",inputs=update_motionest_inputs_v1(uid))
        try:
            runtime=build_runtime_motionest_context(uid,reference_date=REFERENCE)
            payload=runtime.model_dump(mode="json")
            write(root/"runtime"/f"{p['id']}.json",payload)
            summary=runtime.context.training_history_summary
            check("history_count_preserved",summary.activity_count==len(p["activities"]),summary.activity_count)
            expected_sports={"swimming" if s=="lap_swimming" else "strength" if s=="strength_training" else "running" if s=="trail_running" else s for s in p["sports"]}
            actual=set(runtime.context.athlete_state.sports_observed)
            check("sports_not_collapsed_to_running",expected_sports<=actual,sorted(actual))
            check("subjective_pain_preserved",runtime.context.subjective_context.pain_or_injury.affecting_training==bool(p.get("pain")))
            row.update(activity_count=summary.activity_count,sports_observed=sorted(actual),confidence=runtime.context.data_confidence.training_history,recent_sessions_per_week=runtime.context.athlete_state.observed_sessions_per_week_last_28_days,limitations=[x.model_dump(mode="json") for x in runtime.context.integration.limitations],missing_data=runtime.context.data_confidence.missing_data_codes,stale_data=runtime.context.data_confidence.stale_data_codes)
        except Exception as e:
            check("runtime_build",False,type(e).__name__+": "+str(e))
        results.append(row)
    write(root/"synthetic-accounts.json",synthetic_accounts)
    write(root/"runtime-results.json",results)
    return results


def serve(root: Path) -> None:
    isolate(root)
    from garmin_coach.dashboard import DashboardHandler,ThreadingHTTPServer
    print("Synthetic Motionest only: http://127.0.0.1:8778",flush=True)
    ThreadingHTTPServer(("127.0.0.1",8778),DashboardHandler).serve_forever()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--serve",action="store_true")
    parser.add_argument("--remeasure",action="store_true")
    args=parser.parse_args()
    args.output=args.output.resolve()
    if args.serve:
        serve(args.output);return
    if (args.output/"synthetic-accounts.json").exists() and not args.remeasure:
        raise SystemExit("Choose a fresh evaluation output directory; existing accounts are preserved.")
    profiles=json.loads((args.output/"profiles.json").read_text(encoding="utf-8")) if args.remeasure else generate(args.output)
    rows=import_and_measure(args.output,profiles)
    print(json.dumps(dict(profiles=len(rows),activities=sum(r.get("activity_count",0) for r in rows),failed_checks=sum(not c["passed"] for r in rows for c in r["checks"])),ensure_ascii=True))


if __name__=="__main__":main()
