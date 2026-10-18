# Stabilisation ciblée de l’idempotence de livraison Garmin

Base : main propre à `c97b0b610c04b399a5aa43239db1087fd841f484`.
Branche isolée : `codex/motionest-delivery-idempotence`.

## Cause et contrat retenu

Dans `publish_accepted_session`, la branche qui retrouve un événement calendrier
réécrivait systématiquement `updated_at` et le fichier, même si le statut, l’ID
d’événement et l’erreur n’avaient pas changé. `_now()` ne conserve que les secondes :
deux appels dans la même seconde masquaient la différence ; franchir une seconde
rendait `second == first` faux. Le chemin de vérification encore en attente
présentait la même actualisation sans changement d’état.

Preuves du contrat existant :

- Le test original `test_delivery_persists_ids_and_is_idempotent` exige l’égalité
  complète des réponses, y compris l’horodatage, un upload unique et une seule
  programmation avec les mêmes identifiants.
- Le module indique persister les faits confirmés ; `_public_record` expose
  directement le `updated_at` de cet état persistant. Aucun contrat de livraison
  consulté ne définit ce champ comme journal de chaque tentative.
- `created_at` et `schedule_requested_at` distinguent déjà création et demande de
  programmation. Les relances en attente ne constituent pas de nouvelle demande.
- `test_double_acceptance_is_idempotent` conserve aussi `decided_at` et une seule
  décision lors d’une acceptation identique ; le code retourne la décision stockée.

Le contrat retenu et désormais précisé dans la docstring est donc : `updated_at`
date le dernier changement des faits persistés, et non chaque vérification.
Cela ne supprime pas la revérification du calendrier ni la persistance d’un
changement réellement observé.

## Reproduction contrôlée

Le helper existant `_now` est remplacé par monkeypatch dans le test, sans nouvelle
horloge ni dépendance : première publication à `2026-10-18T18:31:16+02:00`, puis
second appel à `2026-10-18T18:31:17+02:00`. Le scénario utilise exclusivement
VerifiedClient et la proposition synthétique, avec stockage temporaire par compte.
Le test échoue sur le code de main avant correction : seul updated_at diffère.
Le même test passe après correction. Aucun sleep ni délai ajouté à la suite.

## Correction minimale

- `garmin_coach/garmin_delivery.py` : comparer les faits confirmés avant mise à
  jour ; ne pas horodater ni écrire un événement inchangé. Même règle pour une
  vérification qui reste en attente. Les uploads, requêtes de programmation,
  vérifications calendrier, contrôles de date et d’acceptation restent en place.
- `tests/test_garmin_delivery.py` : conserver toutes les assertions originales,
  contrôler l’horloge et interdire une écriture sur le deuxième appel inchangé.
  Le test d’attente compare aussi toute la réponse après changement de seconde.
  Quatre cas supplémentaires vérifient la persistance et l’horodatage lorsque
  l’attente devient confirmée, l’ID calendrier change, une erreur est effacée ou
  une confirmation n’est plus retrouvée. Toujours un upload et une programmation.
- Le présent rapport.

Aucun contrat sportif, générateur, mécanisme d’import, synchronisation ou
fonctionnement d’authentification modifié. Aucun moteur de livraison ajouté.

## Résultats

| Validation finale | Réussis | Échecs | Ignorés |
|---|---:|---:|---:|
| Test initial avec horloge contrôlée, après correction | 1 | 0 | 0 |
| Module de livraison Garmin | 10 | 0 | 0 |
| Propositions, acceptation, isolation et confidentialité | 91 | 0 | 0 |
| Suite complète | **843** | **0** | **0** |

Les 91 tests ciblés regroupent test_plan_proposal, test_plan_acceptance,
test_multitenant_security, test_conversation_sport_context et test_history_search_quality.
Les XML sont dans `reports/idempotence-*.xml`, ignorés par Git.
Suite complète : 198,74 s ; module livraison : 3,14 s ; groupe plans/isolation/confidentialité : 24,08 s.
Une comparaison AST confirme la conservation des 26 assertions préexistantes du
module de livraison. Les quatre nouveaux cas portent le total projet de 839 à 843.

Résultats intermédiaires conservés : reproduction avant correction : 1 échec
attendu ; première exécution du module enrichi : 9 réussis, 1 échec causé par
l’oubli de itemType=workout dans une nouvelle fixture synthétique. Fixture corrigée
pour respecter le filtre calendrier existant, sans modification du filtre produit.
Le module final passe ses 10 tests. Quatre avertissements Pydantic préexistants.

## Livraison et préservation

Main reste propre au commit de base ; aucun merge ni push. Un commit local est
créé uniquement après validation finale de la correction sur la branche isolée.
Les archives historiques 1.2AB et la sauvegarde des cinq fichiers 2B sont lisibles ;
les empreintes SHA-256 des cinq entrées de cette dernière sont vérifiées.
La variante historique reste à `04aece083b9e307fa38aba94feab7ea417e00877`.
Les anciens worktrees et leurs changements préexistants restent conservés.
Aucun appel Garmin réel ou IA réel durant cette mission. Arrêt après correction.
