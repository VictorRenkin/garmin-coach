# Motionest — préparation des phases 3 et 4

Ce document consolide les constats et recommandations de l'agent B, en lecture seule pendant la session du 15 octobre 2026. Il ne constitue ni une implémentation ni une validation des phases 3 et 4. Agent B n'a modifié aucun fichier, effectué aucun appel IA/Garmin et exécuté aucun test réel ; les preuves de tests ci-dessous proviennent de leur inspection.

Les appellations désignent ici **phase 3 : représentation et fidélité des séances** et **phase 4 : explication de la fiabilité au sportif**. Elles doivent être confirmées dans la feuille de route : l'ancien document `MOTIONEST_DEV_PLAN.md:349` et `:393` employait déjà les numéros 3 et 4 pour les contrats utilisateur et l'intégration. Ne pas supposer ces périmètres identiques.

## Phase 3 proposée : représentation et fidélité des séances

| Sujet | Existant vérifiable | Écart confirmé |
|---|---|---|
| Contrat de proposition V2 | `garmin_coach/plan_proposal.py:135–162` accepte running, cycling, swimming, strength, cross_training et other ; plafonds de 100 répétitions, 480 minutes, 500 km | Le contrat n'est pas limité à la course, mais `main_set` reste textuel : un couple effort/récupération et un compteur, sans liste de blocs, imbrication ni cibles typées d'allure, watts, distance d'effort ou RPE |
| Ancien contrat de cycle | `garmin_coach/training_cycle.py:18–45` : 12 répétitions, 150 minutes, 25 km, minutes entières et fréquence cardiaque | Représentabilité différente du contrat V2 ; une proposition valide n'est pas nécessairement exportable fidèlement par cet ancien chemin |
| Export Garmin | `garmin_coach/garmin_workouts.py:21`, `RUNNING_SPORT` ; `:127`, `build_running_workout()` ; `:121`, `with_hr` ; publication `:85` et `:101` | Export course/FC à bloc unique ; ne démontre pas un export vélo/natation ni la fidélité de blocs multiples |
| Deux séances le même jour | `validate_plan_proposal()`, `garmin_coach/plan_proposal.py:357–377`, rejet `duplicate_date` ; maximum sept séances vers `:172`, sept dates autorisées vers `:116` | Double séance quotidienne absente du contrat validé |
| Cohérence de durée | `validate_plan_proposal()`, `garmin_coach/plan_proposal.py:357–380` | La somme chiffrée des blocs et sa cohérence avec la durée annoncée ne sont pas validées |

Les protections déjà couvertes doivent être conservées : période, fréquence et jours, signaux médicaux, confidentialité et absence de publication automatique (`tests/test_plan_proposal.py:146`, `:158`, `:168`, `:179`, `:191`, `:240`, `:355`, `:369`). L'export d'intervalles simples et de zones FC est déjà testé (`tests/test_garmin_workouts.py:30`, `tests/test_hr_workouts.py:34`). Il ne faut pas reconstruire ces protections.

Ordre proposé, sans application :

1. Cartographier la fidélité entre contrat V2, ancien cycle et export réel : champs représentables, pertes et refus explicites.
2. Définir un critère empêchant une proposition valide mais non représentable d'être publiée avec une autre signification.
3. Valider seulement ensuite le besoin de blocs typés, d'imbrication, de cibles supplémentaires ou de doubles séances. Prévoir migrations et tests de compatibilité avant toute extension.

Critères d'acceptation proposés : conservation des propositions simples existantes ; vérification explicite de la durée totale ; refus explicite de toute conversion avec perte ; comparaison des structures proposées et exportées sur fixtures ; aucune publication Garmin automatique ou réelle sans autorisation distincte.

## Phase 4 proposée : expliquer la fiabilité

| Sujet | Existant vérifiable | Écart confirmé |
|---|---|---|
| Moteur de confiance | `garmin_coach/data_confidence.py:31–80`, `DataConfidence` | Le moteur existe : ne pas en créer un deuxième pour les libellés |
| Parcours initial | `garmin_coach/first_run_journey.py:57–86`, `_observations()` expose trois niveaux et données manquantes | Présentation encore partielle des conséquences pour les conseils |
| Dashboard | `garmin_coach/dashboard_v2.html:481` affiche nombres et codes joints ; `userFacingCoachText` vers `:453` traduit peu de codes | Absence de libellés complets et de niveaux par composant dans cette présentation |
| Contexte conversationnel | `garmin_coach/runtime_context.py:415–422` transmet niveau/statut/raisons, retire les preuves ; `garmin_coach/motionest_context.py:326–347` conserve niveaux et codes | Le coach dispose de signaux, mais pas de toute la preuve détaillée |
| Instructions du coach | `garmin_coach/coach_chat.py:132–150` : priorité canonique, valeurs nulles et troncature | Aucune exigence systématique d'expliquer composant, raison et conséquence pratique |

Les distinctions déjà testées comprennent historique ancien contre absent (`tests/test_data_confidence.py:159`), fréquence cardiaque (`:229`, `:243`, `:257`), charge partielle (`:297`), récupération (`:325`) et performances (`:311`). `tests/test_motionest_context.py:123` couvre également un historique ancien sans données récentes. Ces cas forment une base existante ; ils ne prouvent pas la compréhension des libellés par un sportif.

Ordre proposé, sans application : établir des libellés lisibles depuis les codes existants, les décliner par composant puis préciser les conséquences dans le coach. La confiance doit rester propre à la donnée ou à la conclusion concernée : un historique ancien disponible n'équivaut pas à une mesure récente de récupération.

Critères d'acceptation proposés : chaque avertissement important indique ce qui manque, pourquoi cela limite le conseil et quelle vérification est utile ; aucune métrique inventée pour remplir un manque ; cohérence entre dashboard et coach ; scénarios distincts d'historique absent, ancien, partiel et récent ; conservation des niveaux calculés par le moteur existant.

## Limites et décision à prendre

Aucun développement des phases 3 ou 4 n'est effectué. La priorité proposée est la fidélité des contrats et exports avant l'ajout de séances complexes, puis l'explication de la confiance à partir du moteur existant. La nomenclature des phases et leur périmètre restent à valider avant exécution.
