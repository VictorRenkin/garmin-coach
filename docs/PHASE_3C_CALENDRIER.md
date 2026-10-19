# Phase 3C — Doubles journées et cohérence du calendrier

Base de développement : `233a5e1360ee171c8b4ee2ac86e4a8cb178170f1`,
phase 3B intégrée localement par l'orchestrateur. Branche isolée :
`codex/motionest-calendar-3c`. La préparation `d6ff172` a été réutilisée ;
aucun nouvel audit général des disciplines ou de Garmin.

## Périmètre livré

Le pipeline existant `AvailabilityV1` → `PlanConstraintsV1` →
`PlanGenerationInputV2` → `PlanProposalV2` → acceptation → livraison est étendu.
Le plafond reste **sept séances par semaine**, avec au plus **deux par date**.
Le comportement par défaut reste une séance par jour. Aucune augmentation
automatique de la fréquence ou de la charge n'est imposée.

Les autorisations de double journée sont des déclarations structurées :

- `double_session_days` : liste explicite, unique et ordonnée de jours disponibles ;
- `minimum_recovery_hours` : repos minimal déclaré, positif et fini ;
- `max_daily_duration_minutes` : plafond quotidien déclaré, entier et borné
  par les 480 minutes déjà admises pour la représentation d'une séance.

Les deux bornes sont obligatoires si des doubles journées sont autorisées.
Elles ne constituent pas une autorisation médicale ou une mesure physiologique.
Le formulaire des disponibilités les expose dans une section facultative ;
`POST /api/onboarding/availability` les reçoit explicitement. La capacité commune
de confirmation de fréquence est `min(7, jours disponibles + jours doubles autorisés)`.
Les trois variantes d'affichage existantes utilisent le même helper.

Les anciennes disponibilités sans les nouveaux champs gardent leur JSON par
défaut. Les champs nouveaux ne sont sérialisés que lorsqu'ils sont déclarés.
La recommandation et la décision réutilisent la capacité confirmée, sans créer
de moteur de calendrier supplémentaire.

## Identité, horaires et récupération

`PlanSessionV2` ajoute `session_id` stable et `starts_at`, datetime ISO avec offset.
Ces deux champs restent optionnels pour les anciennes séances simples. Si des
identifiants sont utilisés, chaque séance de la proposition doit en avoir un
distinct. Un horaire exige un identifiant et sa date locale doit être celle de
la séance.

Un plan à double journée exige des identités et des horaires pour toutes ses
séances : la vérification peut ainsi inclure la récupération le jour suivant.
L'ordre du tableau doit être l'ordre réel des instants UTC, même si les offsets
diffèrent. Les fins de séance ne doivent pas franchir minuit. La durée retenue
ne sous-estime ni les étapes connues ni la durée annoncée arrondie.

`validate_plan_proposal` conserve les jours autorisés et interdictions canoniques
`medical_no_running` et `medical_no_training`. Les doubles sont refusées pour
`beginner` et `comeback`, une douleur affectant l'entraînement non explicitement
absente, ou une confiance de récupération autre que `medium`/`high`. Les bornes
déclarées ne contournent pas ces contrôles. Le volume quotidien et le repos entre
fin et début de deux séances sont contrôlés localement, y compris à travers les jours.

Le chemin legacy ne génère pas de doubles : si une fréquence dépasse les jours
déclarés, son ancien fallback vers d'autres jours de la semaine est refusé.
Son appariement conserve la priorité au `workoutId`. Une date avec plusieurs
candidats ne suffit plus à associer ou marquer une séance terminée.

## Acceptation unitaire et livraison

Le journal d'acceptation existant reçoit une portée optionnelle `session_id` et
l'empreinte de la séance exacte. Une décision globale reste distincte : elle
n'accepte pas implicitement une nouvelle séance identifiée.

- `POST /api/plan-proposal/decision` reçoit éventuellement `session_id`, avec
  les empreintes de source et de proposition déjà exigées.
- Chaque carte identifiée possède ses boutons de validation et de refus.
- L'état `partially_accepted` ouvre le dashboard mais ne rend envoyables que les
  séances ayant leur accord propre. La prochaine séance affichée respecte ce filtre.
- Un refus ou une demande de révision globale postérieure retire les anciens
  accords unitaires. Un accord global ultérieur ne les restaure pas : une nouvelle
  validation unitaire explicite est nécessaire. L'ordre du journal fait foi,
  même lorsque plusieurs décisions surviennent dans la même seconde.

`POST /api/plan-proposal/session` révise exactement un identifiant, sous contrôle
de l'empreinte complète affichée. L'identité n'est pas modifiable et la proposition
entière est revalidée avant sauvegarde. Le contenu et l'acceptation de la seconde
séance restent distincts ; l'accord de la séance modifiée devient caduc.

Les clés de livraison des nouvelles séances utilisent leur identité et leur
empreinte propre. Un changement d'ordre ne crée donc pas un nouvel upload.
Les anciennes séances sans identité conservent la clé `empreinte_proposition:index`.
Le calcul d'empreinte des anciennes propositions omet uniquement les nouveaux
champs `None`, préservant les anciennes acceptations et identifiants persistés.

La route HTTP Garmin impose l'identité d'une nouvelle séance et vérifie son
accord avant de construire la connexion. Le backend conserve les protections
running-only, passé, changement de date, envoi incertain et absence de doublons.

## Collisions et limites Garmin explicites

Les dates occupées localement sont lues par les lecteurs existants : métadonnées
d'activités et fichiers de plans legacy, y compris une séance déplacée hors de
la semaine du nom du fichier. Seules les dates entrent dans le payload du coach.
Aucun import, stockage historique ou connecteur nouveau n'est ajouté.

Avant tout upload, la livraison vérifie un calendrier de forme reconnue. Une
activité ou programmation externe à la date demandée provoque un refus conservateur,
faute de créneau externe suffisamment fiable. Une autre séance du même plan est
autorisée uniquement si ses identifiants de livraison sont connus et son accord
unitaire reste valable. Un calendrier illisible, une entrée malformée, un type
inconnu ou une date invalide ne sont jamais traités comme un calendrier vide.

Cette vérification reste bornée aux données disponibles et aux formes simulées ;
elle ne prouve pas l'exhaustivité d'une API réelle et ne supprime aucun événement.
**Les horaires et le repos sont validés dans Motionest. Le mécanisme Garmin
existant ne transmet que la date**, sans preuve de transmission de 08 h/16 h.
L'interface indique de vérifier l'heure dans Garmin Connect et sur la montre.
Aucun développement de 3D ni essai API/montre réel n'a été effectué.

## Cohérence conversationnelle et IA réelle

`CalendarRequest` représente seulement une intention datée :
`needs_clarification`, `not_appropriate`, `blocked` ou `ready_for_proposal`.
Elle impose `action=none` et ne peut accepter, déplacer ou publier une séance.
Le coach reçoit le calendrier V2 local borné à sept séances, les disponibilités
canoniques et les dates locales occupées, sans identifiant de compte ni secret.
La préparation et la validation V2, puis l'accord de chaque séance restent nécessaires.

Le banc `scripts/validate_calendar_3c.py` exige `--real`, force `max_retries=0`,
plafonne ses appels au nombre de cas sélectionnés et isole les comptes synthétiques.
Un test entièrement hors ligne exécute les cinq cas avec un faux transport.

| Appel réel | Résultat technique | Vérification fonctionnelle |
|---|---|---|
| Demande explicite autorisée | Réussi | Intention datée prête à proposer, aucune mutation |
| Débutant | Réussi | Refus d'une double journée inappropriée |
| Demande ambiguë | Réussi | Clarification, aucune date inventée |
| Séance externe existante | Réussi | Collision datée, aucune suppression ni publication |
| Génération V2 à deux séances | Réussi | Deux IDs distincts, même date, 08 h/16 h avec offset, validation, stockage, accord unitaire, conversion et livraison simulée idempotente |

**5/5 réussites techniques et 5/5 fonctionnelles**, cinq appels exacts :
**22 345 tokens d'entrée + 1 466 de sortie = 23 811 tokens**.
Aucun appel Garmin réel. Preuve locale ignorée : `reports/phase-3c-real.json`.

## Tests et livraison

- Backend initial, sécurité et compatibilité : **179 réussis, 0 échec, 0 ignoré**.
- Développement final et banc hors ligne : **30 réussis, 0 échec, 0 ignoré**.
- Conversation/UI/backend : **152 réussis, 1 échec, 0 ignoré**. L'échec concerne
  `test_coach_status_and_config_http_endpoints_do_not_return_key`, interruption
  HTTP locale Windows déjà observée avant 3C. Le test isolé et le banc hors ligne
  passent ensuite : **2 réussis, 0 échec, 0 ignoré**. Aucune assertion ni logique
  d'authentification n'a été modifiée pour contourner cet incident.
- Qualité indépendante : **22 cas**, ciblés élargis **131 réussis, 0 échec, 0 ignoré**.
  Trois calendriers malformés initialement pris pour vides ont été corrigés au
  point de parsing ; les régressions vérifient zéro upload et programmation.
- Suite complète finale sur `febcab26e53192ddc9d934f6ce5b8da4895250f1` :
  **981 réussis, 0 échec, 0 ignoré**, 182,18 s, quatre avertissements Pydantic
  préexistants du SDK Garmin. Aucun incident HTTP sur cette exécution complète.
  La livraison ajoute uniquement le présent rapport à cet arbre applicatif et
  de tests. Preuve : `reports/phase-3c-final-full.xml` du worktree qualité.

QA navigateur par l'orchestrateur, sur le HTML final et routes entièrement
simulées : deux cartes datées affichent 08 h/16 h avec offset ; un clic d'accord
envoie un seul POST visant `morning` et les empreintes ; seul le matin devient
envoyable, le soir exige encore son accord. La note heure locale/date Garmin est
visible. Le parcours produit confirme aussi la capacité de deux séances pour
un jour disponible explicitement autorisé en double : choix 1 et 2 seulement.
Console : zéro erreur et zéro avertissement. Aucun POST Garmin, aucune requête
externe ni erreur de page. La session de navigateur est fermée.
Preuves ignorées dans le worktree de l'orchestrateur :
`reports/phase-3c-ui-result.json`, `phase-3c-ui-frequency-result.json` et captures
associées.

Fichiers applicatifs : `availability.py`, `planning_decision.py`, `plan_proposal.py`,
`plan_acceptance.py`, `garmin_delivery.py`, `training_cycle.py`, `first_run_journey.py`,
`dashboard.py`, `dashboard_v2.html` et `coach_chat.py`.
Tests : `tests/test_calendar_3c.py` et `tests/test_calendar_3c_quality.py`.
Banc : `scripts/validate_calendar_3c.py`. Le présent rapport documente la livraison.

Main reste à la livraison 3B. La branche 4A, les sauvegardes, l'ancienne variante
historique et les autres worktrees sont préservés. Aucun push, aucune intégration
automatique de 3C, aucun développement de 3D/4A ni nouveau générateur sportif.
