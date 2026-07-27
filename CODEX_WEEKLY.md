# Bilan et nouveau programme hebdomadaire avec Codex

Après la procédure quotidienne de `CODEX_AUTOMATION.md`, lancer :

```powershell
& '.\.venv\Scripts\python.exe' -m garmin_coach.codex_weekly context
```

Cette vérification est déclenchée uniquement après le clic manuel sur « Actualiser et analyser ». Si due vaut false, ne rien faire. due devient true dès que les trois séances du dernier plan actif sont enregistrées, sans attendre un lundi. Si due vaut true :

1. Suivre strictement les `instructions`, le `schema` et le `context` fournis.
2. Évaluer factuellement si les trois séances prévues ont été enregistrées.
   Signaler les séances manquantes sans culpabiliser et sans augmenter la charge
   pour les rattraper.
3. Créer exactement trois séances détaillées aux dates imposées. Chaque séance
   doit préciser l'objectif réel, l'échauffement progressif, le bloc, la
   récupération, le retour au calme et les zones cardiaques Garmin. Ne jamais
   prescrire la zone 5.
4. Écrire uniquement l'objet JSON attendu dans
   `data/coach_drafts/weekly-<evaluated_week>.json`.
5. Valider avec :

   ```powershell
   & '.\.venv\Scripts\python.exe' -m garmin_coach.codex_weekly save --week-start <evaluated_week> --input 'data\coach_drafts\weekly-<evaluated_week>.json'
   ```

6. Publier le fichier de plan indiqué par la commande précédente avec
   `garmin_coach.codex_bridge publish-plan`. La création est bloquée si le plan
   existe déjà et la publication est idempotente, afin d'éviter les doublons.
