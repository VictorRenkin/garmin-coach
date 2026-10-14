# Motionest — capacités sportives, préparation de la phase 2

Cette matrice consolide la lecture seule de l'agent B durant la session du 14 octobre 2026. Elle ne constitue pas un nouvel audit ni une implémentation de phase 2. « Implémenté et testé » signifie code et tests locaux/simulés identifiés ; aucune publication réelle Garmin n'a été vérifiée durant cette lecture.

## Matrice

| Discipline | Lecture | Analyse | Objectifs | Planification | Publication/exécution |
|---|---|---|---|---|---|
| Course | Implémenté et testé | Implémenté et testé | Implémenté et testé | Implémenté et testé | Implémenté et testé par simulation ; Garmin réel non vérifié |
| Trail | Lecture générique implémentée ; classification spécifique non vérifiée par un test identifié | Implémenté mais incomplet : métriques course et dénivelé, sans moteur trail dédié | Implémenté mais incomplet : distance explicite testée, Riegel exclu hors route | Implémenté mais incomplet : moteur course commun | Implémenté mais incomplet : publication de type running ; Garmin réel non vérifié |
| Vélo | Implémenté et testé | Implémenté mais incomplet : agrégats génériques et contexte coach, aucun moteur vélo spécifique | Absent | Absent | Absent |
| Natation | Implémenté et testé | Implémenté mais incomplet : agrégats génériques et contexte coach, aucun moteur natation spécifique | Absent | Absent | Absent |
| Football (`soccer`) | Implémenté et testé | Implémenté mais incomplet : agrégats génériques, aucune analyse football dédiée | Absent | Absent | Absent |
| Marche, randonnée, force, padel, tennis, sports inconnus | Lecture générique implémentée et testée | Implémenté mais incomplet : disponibilité variable des métriques et agrégats génériques | Absent pour les moteurs dédiés | Absent pour les moteurs dédiés | Absent pour les moteurs dédiés |

La prise en compte d'une discipline dans la charge croisée du coureur ne signifie pas qu'un générateur de séances de cette discipline existe. Un objectif déclaré dans la mémoire ne démontre pas non plus l'existence d'un moteur d'objectif sportif dédié.

## Preuves locales consolidées

Les références ci-dessous sont relatives à la racine du dépôt pour rester utilisables après intégration. Les numéros correspondent au snapshot examiné par l'agent B.

| Capacité ou limite | Code | Tests ou preuve complémentaire |
|---|---|---|
| Import d'activités sans filtre course | `garmin_coach/history_pagination.py:84`, appel `client.get_activities` vers ligne 116 | `tests/test_history_pagination.py:219` (sports variés/inconnus), `:325` (confidentialité) |
| Détails multisport et équipement ; FC/puissance selon présence ; piscine sans météo | `garmin_coach/multisport_details.py:305`, `plan_activity_endpoints()` | `tests/test_multisport_details.py:183`, fixtures vélo `:120`, piscine `:133` |
| Classification et agrégats par sport | `garmin_coach/training_history_summary.py:246`, `sport_classification()` ; `:278`, `_distance_relevant()` ; `:320`, `_window()` ; `:679`, `_sport_summaries()` | `tests/test_training_history_summary.py:82` (multisport), `:96` (padel/tennis) |
| Analyse course : sorties longues, allure, intensité, performances | `garmin_coach/training_history_summary.py:603`, `_running_summary()` ; `:460`, `_intensity()` ; `:550`, `_performance_evidence()` | `tests/test_training_history_summary.py:230`, `:249`, `:277` |
| Trail classifié comme running ; objectif distance explicite | `garmin_coach/training_history_summary.py:246` ; `garmin_coach/goal.py:22` | `tests/test_goal.py:49` ; aucun test spécifique de classification trail identifié |
| Référence Riegel non disponible hors route | `garmin_coach/goal_evidence.py:345` | `tests/test_goal_evidence.py:174`, `tests/test_goal_assessment.py:297` |
| Planification orientée course, sans champ sport dans la séance | `garmin_coach/training_cycle.py:18`, `PlannedSession` ; instructions `:77`, `:99` ; `garmin_coach/planning_decision.py:342`, entrées course `:371` | Autres disciplines prises en compte comme charge, sans conversion en kilomètres de course ; subjectif `planning_decision.py:276` |
| Génération et publication running | `garmin_coach/garmin_workouts.py:21`, `RUNNING_SPORT` ; `:127`, `build_running_workout()` ; `:85`, `publish_plan()` ; `:101`, `upload_running_workout()` | `tests/test_garmin_workouts.py:30`, `:54` ; `tests/test_hr_workouts.py:34`, `:64`, `:89` ; publication réelle non vérifiée |
| Sanitisation IA multisport | `garmin_coach/ai_analysis.py:91`, `_sport_for_ai()` ; `:118`, `_sanitized_metrics()` ; `:204`, `_compact_details_for_ai()` | Supports multisport présents ; ne prouve pas une synchronisation multisport courante |

## Limite de fraîcheur à traiter avant extension

`garmin_coach/sync.py:30` filtre les activités avec `is_running`. L'import historique est multisport, mais la synchronisation courante reste orientée course. La présence de données vélo ou natation dans l'historique ne garantit donc pas leur actualisation continue. La fraîcheur multisport est **implémentée mais incomplète** ; ne pas promettre une couverture continue sur la seule preuve de l'import.

## Travaux proposés, sans exécution

1. Définir les disciplines officiellement promises par le produit et leur niveau de support.
2. Traiter d'abord la fraîcheur de synchronisation multisport, en réutilisant l'import et les mécanismes de confidentialité existants.
3. Clarifier et tester les classifications trail et les alias football/soccer.
4. Définir les contrats propres au vélo et à la natation seulement après validation de leur périmètre ; distinguer lecture, analyse et génération.
5. Valider séparément les générateurs et la publication par discipline. Les tests simulés actuels ne remplacent pas une validation Garmin réelle autorisée.

Aucun code modifié, test réel, appel réseau ou appel IA réalisé par l'agent B. Aucun développement de phase 2 réalisé dans cette session.
