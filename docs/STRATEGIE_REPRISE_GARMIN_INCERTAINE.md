# Stratégie proposée de reprise Garmin incertaine

Base : `4de172b0ac2cfd082ef4b3db04122329071d201c`.
Branche : `codex/motionest-garmin-uncertain-strategy`.
Statut : proposition documentaire uniquement, aucun changement de livraison.

## Preuves réutilisées

`docs/VALIDATION_3D_HORS_LIGNE.md` conserve la reproduction et la validation
du correctif `5230807` : huit cas hors ligne réussis après correction.
La matrice `35df39f`, `docs/MATRICE_3D_APRES_INTEGRATION_3C.md`, reste la
référence pour distinguer contrat local, SDK, API privée, API officielle et montre.
Aucun nouvel essai n'est exécuté ici.

La caractérisation antérieure, entièrement fictive, retourne `{}` après chaque
création simulée : deux tentatives explicites donnent deux créations fictives,
deux erreurs `workout_id_missing`, zéro programmation et un store local vide.
Preuve antérieure hors suivi : `reports/phase-3d-lost-upload-ack.json` dans le
worktree `motionest-3d-offline-validation`. Ce résultat ne décrit aucun incident
observé chez Garmin et ne démontre pas une panne du service réel.

## Ce que sait le code actuel

| Situation | Preuve locale et comportement | Limite |
|---|---|---|
| Aucun record | Une séance acceptée peut être envoyée après les contrôles existants | L'absence de record ne distingue pas une tentative antérieure sans réponse |
| ID d'upload reçu et sauvegardé | Relecture et programmation réutilisent cet ID | Une interruption avant sauvegarde rejoint le cas incertain |
| Upload sans ID ou exception de transport | Pas de programmation ; aucune intention persistée avant l'upload dans ce chemin | Création distante possible ; un nouvel appel peut recréer |
| Programmation demandée, ID connu | `schedule_requested_at`, date et ID persistés avant l'appel ; confirmation calendrier ou pending sans renvoi aveugle | Pending ne prouve pas une présence sur montre |
| Même séance confirmée par événement exact | Retour idempotent sans upload ni programmation | Ne retrouve pas un upload inconnu par son nom |

Sources : `garmin_coach/garmin_delivery.py`, `_session_delivery_key`,
`_checked_calendar_entries`, `_workout_name`, `publish_accepted_session`.
La clé identifiée combine `session_id` et `session_fingerprint`; le chemin legacy
emploie empreinte de proposition et index. Ces clés locales ne sont pas transmises
comme clés d'idempotence reconnues par le serveur. `_WRITE_LOCK` protège le
processus courant, sans prouver une coordination entre plusieurs processus.

Tests existants dans `tests/test_complex_double_day_3d.py` :

- `test_interruption_after_upload_id_persistence_resumes_without_a_second_upload` ;
- `test_schedule_ack_lost_after_simulated_remote_success_is_verified_without_resending` ;
- `test_confirmation_absent_remains_pending_then_reconciles_from_reloaded_store` ;
- `test_upload_without_confirmed_id_never_schedules_or_claims_created_workout`.

## Recherche : capacité du SDK, pas garantie serveur

Lecture seule du SDK installé `garminconnect 0.2.40`, sans requête réseau :

| Méthode | Code constaté | Ce qu'elle ne garantit pas |
|---|---|---|
| `get_workouts(start=0, limit=100)` | Lecture de liste via `connectapi`, offset et limite | Exhaustivité, ordre stable, visibilité immédiate, recherche par clé client |
| `get_workout_by_id(workout_id)` | Lecture d'un workout par ID connu | Retrouver un ID jamais reçu |
| `get_scheduled_workout_by_id(scheduled_workout_id)` | Lecture par ID de programmation connu | Recherche du workout non programmé ou inconnu |
| `upload_workout(payload)` | POST `connectapi`, puis lecture JSON | Déduplication serveur ou clé idempotente documentée |
| `schedule_workout(workout_id, date_str)` | POST avec date `YYYY-MM-DD` | Export de l'heure locale `starts_at` |

Fichier inspecté : environnement `.venv/Lib/site-packages/garminconnect/__init__.py`.
Les docstrings et l'implémentation décrivent le client privé actuel, pas un
engagement officiel Garmin. Aucun endpoint n'est appelé ici. Aucun nouveau
contrat partenaire officiel n'est affirmé : recherche et idempotence éventuelles
devront être établies dans la documentation partenaire effectivement accessible.

Un calendrier vide ne prouve pas l'absence d'un workout uploadé mais non programmé.
Une liste vide, partielle ou consultée trop tôt ne prouve pas non plus l'absence
de création. Deux séances peuvent avoir le même nom `MOT JJMM RUN`, la même durée
et même structure. Un candidat ressemblant reste un candidat, pas une attribution
certaine. Les IDs locaux ne sont pas des IDs Garmin ; ID d'événement et ID de
workout sont distincts. Aucune adoption automatique par nom n'est sûre ici.

## Stratégie minimale proposée, non implémentée

1. **Avant une future tentative**, conserver durablement une intention isolée
   par utilisateur, identité et empreinte de séance, avec identifiant de tentative
   local, date et empreinte du payload canonique. Ne pas y stocker les secrets
   d'authentification. Cette écriture préalable et son verrouillage durable
   nécessitent une mission distincte ; ils n'existent pas aujourd'hui pour
   l'upload sans ID. Après interruption, une intention sans preuve d'absence
   d'envoi reste incertaine.
2. **Une seule émission applicative**, après vérification explicite de la politique
   de retries du transport POST retenu. Le nombre d'appels Python et une clé locale
   ne garantissent pas une seule création distante. Le présent examen n'établit
   pas la politique de retries de toutes les couches réseau.
3. **ID confirmé** : le sauvegarder avant tout autre effet, puis réutiliser les
   gardes actuelles d'accord courant, identité, date, sport et collisions. Si la
   sauvegarde échoue, arrêter les effets ; ne pas effectuer un nouvel upload.
4. **Réponse perdue, invalide ou sans ID** : bloquer le renvoi de cette tentative,
   sans annoncer création ni absence de création. Une révision changeant
   l'empreinte ne doit pas contourner l'incertitude d'une tentative précédente.
   Ce blocage est proposé ; il n'est pas une fonctionnalité actuelle.
5. **Inspection future bornée**, seulement après autorisation du compte : une
   passe en lecture seule, au plus trois pages de cent workouts, puis au plus
   cinq lectures de détail de candidats. Ces plafonds sont un budget proposé,
   pas une garantie de complétude. Aucun polling continu ni retry de publication.
   Privilégier un ID exact déjà connu. Limite atteinte, pagination incertaine,
   données incohérentes, zéro ou plusieurs candidats donnent « non résolu ».
   Une comparaison de structure aide à présenter les candidats, sans jamais
   autoriser leur adoption automatique.
6. **Sortie de l'incertitude** : preuve distante d'identité définie et validée
   pour l'API retenue, ou revue humaine explicite du compte. Une revue ne
   transforme pas un nom similaire en preuve. Si l'identité reste indécidable,
   conserver le blocage. Un nouvel essai exige une décision distincte informée
   du risque résiduel de doublon et un accord courant. Aucune suppression ou
   substitution distante n'est proposée pour débloquer la reprise.

Message produit proposé : « Garmin n'a pas confirmé l'identifiant. Un entraînement
peut déjà avoir été créé. N'effectue pas de nouvel envoi avant vérification. »
Cette formulation n'est pas déployée par la présente mission.

## Bornes et prochaine étape sûre

L'idempotence après ID persisté reste prouvée dans les scénarios simulés documentés.
Elle ne couvre ni l'upload accepté sans réponse, ni la panne entre réception de
l'ID et écriture durable, ni une garantie exactly-once serveur. Ne pas réaccepter
implicitement une séance révisée, élargir les anciens IDs autorisés ou affaiblir
l'isolation des comptes pour résoudre cette incertitude.

Une mission ultérieure peut valider hors ligne l'intention durable, le blocage
après interruption et les bornes d'inspection avant toute expérimentation distante
expressément autorisée. Le choix API officielle/chemin privé et les preuves
d'identité doivent être fixés avant d'implémenter un rapprochement. Montre et
horaires 08 h/16 h demeurent hors preuve.

Livrable : ce seul document. Aucun fichier applicatif ou test modifié ; aucun
nouvel essai, appel IA, Garmin réel, suppression, push ou changement de main.
Les résultats de tests cités proviennent des campagnes antérieures.
