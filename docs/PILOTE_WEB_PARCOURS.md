# Pilote Web — compléments de reprise utilisateur

Base : `839fd052d610445bad4c71e771a26524deb1cafb`, branche isolée `codex/motionest-pilot-web-journeys`. Le parcours des vingt étapes et les preuves de la phase 6 sont réutilisés depuis `PARCOURS_WEB_PHASE_6.md`. La référence de suite complète annoncée à la mission est 1 107 tests ; elle n'a pas été réexécutée ici.

## Trou utile complété

Les succès d'inscription, onboarding, découverte, conversation sans historique, connexion tardive, multisport et séances complexes/doubles avec décisions individuelles sont déjà démontrés. La reprise d'import conserve déjà la date de référence dans `tests/test_garmin_setup_endpoints.py::test_setup_retry_preserves_the_original_history_reference_date`.

Le complément ajoute une seule chaîne HTTP paramétrée dans `tests/test_pilot_web_journeys.py::test_late_connection_error_keeps_discovery_and_allows_explicit_retry` :

| Incident synthétique | Réponse existante vérifiée | Reprise vérifiée |
|---|---|---|
| Code MFA requis | 409, `mfaRequired=true` | Nouvelle soumission explicite avec code |
| Identifiants refusés | 401 | Nouvelle soumission explicite |
| Connexion Garmin indisponible | 503 | Nouvelle soumission explicite |
| Limitation temporaire | 429 | Nouvelle soumission explicite |

Pour chaque cas, le vrai `DashboardHandler` et les sessions signées sont utilisés. Avant la reprise : aucun setup ni token créé, contexte encore en découverte, dashboard et formulaire accessibles ; consulter ces pages ne relance pas l'authentification. Après la soumission volontaire : 202 et un seul lancement du setup. Profil, entrées canoniques, mémoire et messages restent identiques octet par octet ; Bob ne reçoit aucun token. Les erreurs publiques ne reproduisent ni mot de passe ni détail fournisseur synthétique.

Composants existants utilisés : `dashboard.py` route POST `/api/garmin/connect` et ses gestionnaires d'erreur, `coach_context`, `stored_tokens_available`, sauvegardes et fixtures de `test_discovery_phase5_quality.py`. Aucun changement applicatif nécessaire : aucune anomalie reproduite dans ces quatre parcours.

## Résultat et limites

Commande : `.venv/Scripts/python.exe -m pytest tests/test_pilot_web_journeys.py -q --junitxml=reports/pilot-web-journeys.xml`.

**4 réussis, 0 échec, 0 ignoré en 5,10 s**, quatre avertissements SDK Pydantic préexistants. XML local : `reports/pilot-web-journeys.xml`. Seuls ce test et ce document sont ajoutés.

Verdict ciblé : aucun blocage de reprise HTTP identifié. Ce complément ne valide pas l'ergonomie graphique pour une personne non technicienne, ni les délais réellement imposés par Garmin, ni le transport d'e-mail, la sémantique d'un modèle réel ou une montre. Les transports Garmin et le démarrage de setup sont simulés ; aucun historique n'est artificiellement ajouté au produit. Les preuves navigateur de l'autre agent restent nécessaires pour le verdict global pilote.

Aucun appel Garmin/IA réel, aucun fichier `mobile-app` ouvert/testé, aucune modification de `main`, aucun push. Les anciennes branches et sauvegardes restent conservées.
