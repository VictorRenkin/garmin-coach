# Motionest — validation du Coach conversationnel et de la mémoire V2

Date de validation : 7 octobre 2026
Branche : `codex/post-import-journey-v1`

## Résultat

Le Coach reste responsable de la discussion, de la clarification et de l'explication. Le backend fournit un contexte compact, valide les sorties structurées, persiste uniquement les déclarations utilisateur ancrées dans leur message et conserve les confirmations existantes pour toute décision importante.

## Architecture retenue

- Un appel IA unique produit le message, l'état conversationnel, l'action éventuelle et les mises à jour de mémoire.
- `conversation_state` vaut `ready`, `needs_clarification` ou `awaiting_confirmation`.
- `clarification_topic` identifie le sujet attendu sans transformer le chat en formulaire.
- Une contrainte explicite `medical_no_running` ou `medical_no_training` bloque les actions d'entraînement incompatibles. Une gêne ambiguë ne déclenche pas ce blocage.
- GoalAssessment reçoit seulement les catégories `goal`, `constraint` et `current_signal` actives et pertinentes.
- PlanningRecommendation reçoit seulement `constraint`, `current_signal`, `unrecorded_activity` et `preference`.
- Une mémoire conversationnelle ne modifie jamais directement Goal, Availability, fréquence confirmée, plan ou publication Garmin.

## Format mémoire avant/après

Avant : tableau de chaînes enrichies uniquement de `fact` et `recorded_at`.

Après : store `schema_version: 2`, contenant des éléments avec :

- `id`, `category`, `key`, `text` ;
- `source`, `source_message_id` ;
- `scope`: `durable`, `current` ou `event` ;
- `status`: `active`, `superseded` ou `resolved` ;
- `recorded_at`, `updated_at`.

Les opérations acceptées sont `add`, `upsert`, `resolve` et `supersede`. L'ancien format reste lisible sans réécriture destructive. Un fichier corrompu produit une projection vide sûre et n'est jamais écrasé silencieusement par une nouvelle écriture.

## Compactage du contexte

- Maximum de messages récents envoyés : **8**.
- Maximum de mémoires sélectionnées : **12**.
- Taille maximale de la projection mémoire : **1 800 caractères**.
- Les signaux `current` âgés de plus de 45 jours et les événements de plus de 14 jours sont exclus du contexte actif.
- Les contraintes et signaux courants sont prioritaires pour une demande liée à une douleur, une blessure, une fatigue, une maladie, un sommeil perturbé ou une reprise.
- Les disponibilités et objectifs sont priorisés seulement lorsque la demande les concerne.

## Tests conversationnels déterministes

| Cas | Preuve |
| --- | --- |
| Préférence durable | Stockage V2 durable, disponible au prochain échange, un seul appel IA mocké. |
| Signal temporaire | Stockage `current_signal`, sans diagnostic inventé. |
| Résolution | Le signal actif devient `resolved` et disparaît du contexte actif. |
| Blessure ambiguë | `needs_clarification`, une question utile, aucune fréquence/action arbitraire. |
| Reprise en amélioration | Mise à jour factuelle possible sans blocage automatique. |
| Interdiction explicite | Action d'entraînement incompatible rejetée par le chat et PlanningRecommendation. |
| Correction de disponibilité | Ancienne déclaration supersédée, une seule vérité active, aucun contrat canonique modifié sans confirmation. |
| Demande mixte | Un déplacement suivi d'un signal de fatigue n'est pas court-circuité par l'action locale. |
| Conversation longue | 50 messages réduits aux 8 derniers avec conservation de la mémoire structurée. |
| Isolation | Deux utilisateurs conservent des stores distincts. |
| Store corrompu | Lecture sûre et interdiction d'écrasement silencieux. |
| Erreur/reprise | Même message relancé, sans duplication, avec compteur de retry. |

Résultat ciblé final : **181 réussis, 0 échec, 4 warnings externes `garminconnect`**.

## Suite complète

Commande :

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Résultat : **711 réussis, 0 échec, 4 warnings externes `garminconnect`**, durée **183,06 s**.

## Validation Playwright

Le vrai template `dashboard_v2.html` a été chargé dans Chromium. Les endpoints ont été simulés pour isoler l'interface et ne consommer aucun appel IA.

Validé :

- conversation normale et question de clarification visibles ;
- réponse utilisateur et correction conservées dans le fil ;
- message utilisateur affiché immédiatement ;
- `Le coach réfléchit…` pendant l'attente ;
- `L'analyse prend un peu plus de temps que d'habitude…` après 10 secondes ;
- message échoué conservé avec bouton `Réessayer` ;
- bouton de retry utilisable au clavier avec Entrée ;
- reprise du même message sans duplication ;
- navigation dans la page puis retour au Coach sans perte du fil.

Limite : aucun compte local n'était actuellement dans l'état serveur `dashboard`; le contrôle Playwright a donc utilisé le vrai template avec réponses API isolées. Les routes HTTP réelles `/api/coach/messages` et `/api/coach/retry` sont couvertes séparément par les tests backend.

## Benchmark IA réel limité

Outil manuel isolé : `scripts/benchmark_coach_conversation.py`. Il n'est jamais exécuté par l'application ou les tests, emploie des données synthétiques et supprime son dossier temporaire en fin d'exécution.

- Appels : **6**.
- Modèle réellement routé : `gpt-5.6-luna`.
- Erreurs : **0**.
- Latence totale : **23,164 s**.
- Latence moyenne : **3,861 s**.
- Latence médiane : **3,294 s**.
- Latence min/max : **2,798 / 7,134 s**.
- Input tokens : **19 748**.
- Cached input tokens : **14 800**.
- Output tokens : **981**.
- Total tokens : **20 729**.
- Appels avec cache fournisseur observé : **5 sur 6**.

| Appel | Scénario | État | Latence | Payload | Messages | Mémoire |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | Gêne ambiguë + souhait de 3 séances | clarification | 7,134 s | 329 o | 0 | 0 |
| 2 | Gêne légère, stable, en amélioration | clarification + signal courant | 3,239 s | 640 o | 2 | 0 |
| 3 | Nouveau malaise temporaire | clarification + signal courant | 2,798 s | 1 191 o | 4 | 1 |
| 4 | Résolution du malaise | résolution + clarification genou | 3,741 s | 1 753 o | 6 | 2 |
| 5 | Préférence durable du soir | préférence mémorisée | 2,904 s | 1 848 o | 8 | 1 |
| 6 | 50 anciens messages simulés | clarification utile | 3,348 s | 2 163 o | 8 | 2 |

Le sixième appel prouve que les 50 messages ne sont pas tous transmis : le nombre reste borné à huit, les faits importants anciens restent accessibles par la mémoire, et le payload conversationnel reste compact.

## Télémétrie

Chaque appel structuré enregistre, lorsqu'ils sont disponibles : catégorie, modèle routé, tokens d'entrée, tokens d'entrée en cache, tokens de sortie, total, durée, taille du payload et statut de cache. Les appels du Coach utilisent un timeout de 60 secondes sans retry SDK implicite.

## Limites restantes

- La sélection mémoire est déterministe, sans recherche vectorielle.
- Les signaux expirés sont exclus du contexte mais restent historisés dans le store.
- La promotion d'une déclaration vers Goal, Availability ou SubjectiveContext demande toujours une future action structurée explicite.
- RecoveryState complet et diagnostic médical restent hors périmètre.
- Les actions de plan continuent d'utiliser le système de confirmation existant.
