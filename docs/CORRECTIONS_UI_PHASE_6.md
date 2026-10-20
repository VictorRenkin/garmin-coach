# Deux corrections UI ciblées de phase 6

Base : `9a2c5159f4b8ed351cf74cf293214938336ead90`.
Préparation réutilisée : `PREPARATION_PHASE_6_ANOMALIES.md`, branche
`codex/motionest-phase6-preparation`. Aucun audit responsive général recommencé.

- `DashboardHandler.do_GET` autorise précisément `/favicon.ico` comme ressource
  publique. `_do_GET` sert une petite icône SVG statique, avec type MIME, longueur
  et cache explicites ; aucun contenu utilisateur ou accès fichier dynamique.
  Le HTML annonce cette ressource et son format. Les routes protégées restent
  soumises aux contrôles existants.
- `renderEmptyActivity` remplace le chargement des zones par « Aucune activité
  importée : zones indisponibles. ». Cette mise à jour remplace aussi d'éventuelles
  anciennes zones. Aucune activité, zone ou métrique synthétique n'est ajoutée.

Régressions dans le seul nouveau module `tests/test_ui_phase6_fixes.py` : serveur
HTTP local réel pour favicon sans session/token, SVG valide, profil toujours 401
et dashboard toujours redirigé sans session ; exécution de la vraie fonction JS
dans Node avec éléments DOM minimaux pour états chargement et anciennes zones.
Node disponible est requis, sans test silencieusement ignoré.

Première passe : 49 réussis, un échec du banc lié au décodage Windows de stdout
Node. Correction limitée à `encoding="utf-8"` du subprocess de test.
Passe finale : **50 réussis, 0 échec, 0 ignoré**, 7,39 s, quatre avertissements
Pydantic préexistants, via `pytest tests/test_ui_phase6_fixes.py tests/test_dashboard.py -q`.
Les validations navigateur de combinaison sont réservées à l'orchestrateur :
ce test JS isolé ne prétend pas remplacer un navigateur ou valider le responsive.

Fichiers : `garmin_coach/dashboard.py`, `garmin_coach/dashboard_v2.html`, le
nouveau module de tests et ce document. Aucun contrat sportif, Garmin, historique,
compte ou onboarding modifié. Aucun appel IA/Garmin réel, push ou changement
de main. Aucun autre test existant édité. Les deux fichiers dashboard sont libérés
après ce checkpoint pour l'intégration séquentielle de phase 5.
