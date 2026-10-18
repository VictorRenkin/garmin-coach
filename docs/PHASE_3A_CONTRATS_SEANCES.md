# Phase 3A — Contrats et limites des séances de course

Base : main propre à `24fbf6c3d6b23eb521ae26058a5f7a0795a9f179` (843 tests).
Branche isolée : `codex/motionest-session-limits-3a`.
La livraison 4A `ebd2580` reste distincte, propre et non intégrée à cette branche.
Les documents PREPARATION_PHASES_3_4, EVALUATION_MOTIONEST_2026-10-12 et
AVIS_SIMULES_MOTIONEST_2026-10-12 ont été consultés uniquement sur ces limites.

## Cartographie du chemin réellement concerné

Deux chemins existaient avant cette mission ; aucun troisième n’est ajouté :

1. Proposition V2 : `planning_decision` conserve la fréquence confirmée et les
   contraintes ; `plan_proposal.generate_plan_proposal` produit PlanProposalV2,
   `validate_plan_proposal` contrôle période, nombre, dates, disponibilités et
   interdictions médicales. La proposition est stockée et présentée via le dashboard.
   `record_plan_acceptance_decision` accepte l’identité exacte de la proposition.
   `garmin_delivery.publish_accepted_session` exige cette acceptation, refuse les
   autres sports et les conflits de date, puis convertit avec
   `build_plan_session_workout` et conserve les identifiants/idempotence existants.
   Les routes dashboard vers ces fonctions sont vers les lignes 1933, 1956 et 2000.
2. Legacy : `generate_initial_plan` / `generate_weekly_cycle` produisent PlannedSession.
   Les modifications conversationnelles passent par CoachResponse/SessionPatch,
   un brouillon à confirmer, puis `dashboard.modify_planned_session` revalide la
   séance entière avant stockage. La confirmation legacy peut ensuite appeler
   `replace_future_plan` ; l’acceptation initiale appelle `publish_plan`.
   Le convertisseur actif est la dernière définition de `build_running_workout`
   dans garmin_workouts.py, vers la ligne 127. L’autre définition homonyme
   préexistante n’a pas été nettoyée : aucune refonte dans cette mission.

Le dashboard utilise les descriptions et les durées de la séance ; il n’impose
pas de plafond additionnel sur les récupérations. Les nombres de jours/séances
restent contraints par les mêmes modèles et contrôles. Aucun changement d’interface.

## Limites historiques : preuves et décision

| Sujet | État avant 3A | Décision et preuve |
|---|---|---|
| Sept séances/semaine | Toujours présent : InitialPlan/WeeklyCycle, PlanProposalV2, allowed_dates et planning_decision | Conservé. La semaine a sept jours et V2 impose une séance par date. Tests existants duplicate_session_date/unavailable_day et nouveau test_weekly_limit_stays_at_seven. Doubles journées réservées à 3C. |
| Douze répétitions | Déjà résolu dans V2 : 100 ; encore présent dans PlannedSession et SessionPatch | Alignement legacy sur les 100 déjà admises par V2. Les convertisseurs SDK construisent exactement 15 et 100 répétitions dans les tests, sans troncature. |
| 25 km | Déjà résolu dans V2 : distance optionnelle jusqu’à 500 km ; encore présent dans les deux contrats legacy | Réutilisation du plafond de représentation V2 (500), sans plafond arbitraire intermédiaire. Test d’une séance synthétique de 30 km et rejet de 501. Cette distance reste une estimation descriptive, pas un bloc Garmin ciblé en distance. |
| 150 minutes | Déjà résolu dans V2 : 480 ; encore présent dans les deux contrats legacy | Alignement sur 480 ; effort continu jusqu’à 360, comme V2, au lieu de 100 (nécessaire pour 165 min de bloc sur une séance de 180 min). Tests à 150/25 puis 180/30 et rejet de 481. |
| Récupérations entières | Déjà résolu dans PlanSessionStructureV2 : float ; contrats legacy encore int | Float en minutes dans PlannedSession et SessionPatch. Tests 0.5, 0.75 et 1.5 minute, préservant exactement 30/45/90 s sur les deux adaptateurs. |

Preuves avant correction : `reports/phase-3a-baseline-limit-reproduction.json`,
reproduction sur main sans modification. Les trois récupérations échouent avec
int_from_float ; 15 répétitions avec less_than_equal ; 180 minutes/30 km/165 minutes
d’effort échouent sur les trois plafonds legacy. Il ne s’agissait donc pas d’une
limite Garmin démontrée. La voie V2 admettait déjà ces données.

Les plafonds partagés figurent dans session_limits.py et sont réutilisés par les
trois contrats. Le schéma JSON de PlanSessionV2/PlanProposalV2 est strictement
identique à main (hash `7fd9ffeee7cd601d0df4cb7240e2a24ae5068310671a23fd01a3b2fdb5e98789`).
La factorisation n’élargit donc pas V2 une deuxième fois.

Ces valeurs sont des bornes finies de représentation déjà validées dans le contrat
V2, pas une preuve qu’un sportif devrait effectuer 500 km ou huit heures de course.
Aucune recommandation n’est relevée automatiquement ; les consignes de progression,
les contraintes médicales, les jours et la confirmation utilisateur restent en place.
Les minima legacy (15 min/2 km), phases 3–25 min, récupération maximale 15 min et
cibles cardiaques restent inchangés faute de besoin confirmé dans ce périmètre.

Un garde-fou supplémentaire dans PlannedSession rejette le produit des répétitions
et durées lorsqu’il dépasse 480 minutes : augmenter les bornes individuelles ne doit
pas contourner le plafond de durée exécutée. Il ne vérifie pas toute la fidélité
textuelle/numérique (3B). Le DTO Garmin demande une durée estimée entière en secondes :
seule cette estimation est arrondie ; les valeurs des étapes ne sont pas arrondies
à la minute. Aucun changement d’upload, programmation, identifiant ou idempotence.

## Structure complexe et limites conservées

Le format courant représente échauffement, un bloc homogène répété et retour au
calme. Une récupération positive fait partie de chaque répétition, y compris la
dernière, conformément aux convertisseurs existants ; les scénarios et totaux du
banc explicitent ce choix. Pas d’ajout de liste de blocs ou de répétitions imbriquées.

La nouvelle régression « plusieurs blocs » démontre un refus explicite de blocs
structurés inconnus : StrictContract V2 rejette blocks, et le backend legacy refuse
un patch contenant ce champ sans écrire le fichier. Les consignes du coach et du
générateur legacy signalent cette limite et interdisent une simplification silencieuse.
Un texte libre main_set/bloc_principal peut encore évoquer une séance plus riche
que la structure numérique : sa fidélité générale et les blocs typés restent en 3B.
Les doubles journées restent en 3C. Aucune autre sous-phase développée.

## Tests déterministes

Tous les clients sont simulés ; le nouveau fichier réutilise VerifiedClient et
make_session existants. La validation réelle de l’acceptation est exercée sur un
store temporaire : avant accord, zéro upload ; double acceptation idempotente ;
compte non accepté refusé ; un upload et une programmation après accord.
Une séance cycling acceptée ne contourne pas la protection running-only.

Les cas obligatoires couvrent : séance simple inchangée ; récupérations 30/45/90 ;
bloc multiple refusé ; séance près des anciennes limites et au-delà ; répétitions
15 et 100 ; valeurs négatives/nulles/excessives/non finies ; plafond de durée calculée ;
proposition non acceptée ; répétition d’acceptation ; comptes isolés et confidentialité.
Null reste possible dans un patch pour signifier « aucun changement », mais pas dans
les champs requis de la séance complète. Aucune assertion métier existante supprimée.

- Ciblés finaux (contrats, séances, proposition, acceptation, livraison, isolation,
  confidentialité) : **197 réussis, 0 échec, 0 ignoré** en 27,06 s.
- Première suite complète sur le code final : **873 réussis, 1 échec, 0 ignoré**
  en 208,11 s. Le seul échec est test_coach_status_and_config_http_endpoints_do_not_return_key
  (tests/test_dashboard.py), connexion HTTP interrompue par Windows (WinError 10053)
  sur une requête de configuration non authentifiée. Aucun fichier de ce mécanisme
  modifié en 3A. Le test isolé passe sur la branche et sur main (1/1 chacun).
  Le diagnostic de cause exacte n’est pas étendu dans cette mission.
- Dernière suite complète : **874 réussis, 0 échec, 0 ignoré** en 198,32 s,
  sans changement de code ni assertion HTTP. Les 31 nouveaux cas portent le total
  de 843 à 874. L’intermittence HTTP précédente reste une réserve du banc local.
- Quatre avertissements Pydantic préexistants dans garminconnect.

Première passe : 3 échecs sur la lecture de fixtures avec l’encodage Windows implicite ;
la nouvelle régression lit désormais explicitement l’UTF-8 déjà écrit par le produit.
Aucune modification d’encodage applicatif. Les passes suivantes réussissent (133 puis
196 tests avant les dernières régressions). Une suite complète démarrée avant le
garde-fou a été interrompue volontairement, puis remplacée par une exécution finale
sur le code entier. Elle n’est pas comptée comme validation ni comme échec métier.
Preuves : XML `reports/phase-3a-targeted-validated.xml`, `phase-3a-full-final.xml` (incident HTTP),
`phase-3a-http-isolated.xml` et `phase-3a-full-validation.xml` (dernière suite).

## Appels IA réels : technique et fonctionnel distingués

Le banc `scripts/validate_session_limits_3a.py` exige --real, utilise uniquement des
comptes/plans synthétiques, des répertoires temporaires et les convertisseurs locaux.
Il ne déclenche ni route de publication ni compte Garmin. Chaque réponse doit être
modify_session, viser l’indice exact, conserver la date et les valeurs demandées ;
le patch est appliqué localement, revalidé puis converti et comparé aux secondes,
récupérations, répétitions et à la durée attendues. Ce contrôle n’est pas un envoi.

La première campagne a réussi 5 appels techniquement et **0 fonctionnellement** :
les chiffres étaient exacts, mais l’IA a utilisé session_index=1 pour « séance 1 ».
La convention base zéro était implicite. Une description du champ et une phrase
d’instruction l’explicitent désormais (sans réindexation automatique ni nouveau flux).
La seconde campagne réussit **5/5 techniquement et 5/5 fonctionnellement** :
30 s, 45 s, 90 s, 15 répétitions, puis 180 minutes/30 km. Les cinq séances enregistrées
ont été rejouées hors ligne après ajout du garde-fou : toutes restent valides.

| Campagne | Appels | Tokens entrée | Tokens sortie | Total |
|---|---:|---:|---:|---:|
| Avant clarification d’indice | 5 | 21 710 | 1 808 | 23 518 |
| Après clarification d’indice | 5 | 22 075 | 1 855 | 23 930 |
| Total | **10** | **43 785** | **3 663** | **47 448** |

Rapports ignorés par Git : phase-3a-real.json, phase-3a-real-final.json et
phase-3a-post-guard-replay.json. Les succès finaux restent une mesure ciblée,
pas une garantie que toute réponse future choisira le bon indice.

## Fichiers livrés

- garmin_coach/session_limits.py : plafonds existants partagés.
- garmin_coach/training_cycle.py : contrat legacy, garde-fou et unités dans les consignes.
- garmin_coach/coach_chat.py : patch, unités, limites structurelles et convention d’indice.
- garmin_coach/plan_proposal.py : constantes factorisées, schéma V2 inchangé.
- garmin_coach/garmin_workouts.py : type entier de la durée estimée du DTO.
- tests/test_session_limits_3a.py : régressions et réutilisation des clients existants.
- scripts/validate_session_limits_3a.py : validation IA réelle bornée.
- Le présent rapport.

## Risques, livraison et préservation

Le SDK sérialise les cas testés ; les limites effectives des serveurs Garmin et des
modèles de montres ne sont pas démontrées par ces mocks. Aucune promesse de fidélité
des séances à plusieurs blocs, de ciblage en distance, de sous-seconde ou de publication
réelle à toutes les bornes maximales. Les contrôles V2 de durée à la conversion sont
conservés ; la cohérence exhaustive entre texte, durée annoncée et toutes les étapes
reste en 3B. Aucun nouveau moteur de planification ni pipeline de publication.

Main reste propre à la base. Branche 4A propre et intacte à ebd2580. Les archives
historiques 1.2AB sont lisibles ; les cinq empreintes de la sauvegarde 2B sont vérifiées.
Variante historique `04aece083b9e307fa38aba94feab7ea417e00877` conservée.
Les anciens worktrees et leurs travaux locaux restent accessibles. Aucun push,
aucun appel Garmin réel, aucune modification d’historique/import/synchronisation.
Le commit local de livraison est créé après validation, sans intégration dans main.
