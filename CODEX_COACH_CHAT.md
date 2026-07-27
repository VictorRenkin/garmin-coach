# Dialogue du tableau de bord avec ChatGPT/Codex

Le tableau de bord traite désormais directement les demandes avec la clé API
OpenAI configurée dans `.env`. Il transmet le contexte sportif anonymisé prévu
par l'application. Cette procédure manuelle reste un mode de secours pour les
anciennes demandes encore marquées `pending`.

## Lire les demandes

1. Lancer :

   ```powershell
   & '.\.venv\Scripts\python.exe' -m garmin_coach.codex_bridge pending-requests
   ```

2. Si `requests` est vide, terminer sans modifier le projet.

3. Pour chaque demande, répondre en français, avec des faits issus du projet.
   Ne jamais inventer de douleur, de fatigue ou de donnée. Les conseils sont
   sportifs et informatifs, sans diagnostic médical.

## Adapter un programme

Si la demande nécessite une modification du programme :

1. Lire le plan actif, les analyses disponibles et les données sportives dans
   `data/activity_details`. Ne jamais lire les jetons Garmin, les coordonnées ou
   un mot de passe.
2. Ne jamais modifier une séance dont la date est passée. Conserver aussi toute
   séance déjà réalisée.
3. Créer un plan complet révisé dans
   `data/coach_drafts/plan-<request_id>.json`. Il doit rester cohérent avec
   l'objectif semi-marathon, trois séances par semaine et les zones Garmin.
4. Valider et appliquer la révision avec :

   ```powershell
   & '.\.venv\Scripts\python.exe' -m garmin_coach.codex_bridge apply-revision --current 'data\plans\<semaine>.json' --replacement 'data\coach_drafts\plan-<request_id>.json'
   ```

   Cette commande supprime d'abord les anciennes séances futures du calendrier
   et de la bibliothèque Garmin, puis publie les nouvelles. Ne jamais publier
   directement une révision par une autre commande : cela créerait des doublons.

## Enregistrer la réponse

Écrire un JSON dans `data/coach_drafts/response-<request_id>.json` :

```json
{
  "message": "Réponse claire à afficher dans le tableau de bord.",
  "plan_modifie": false
}
```

Mettre `plan_modifie` à `true` uniquement après une révision Garmin réussie.
Puis lancer :

```powershell
& '.\.venv\Scripts\python.exe' -m garmin_coach.codex_bridge save-response --request-id <request_id> --input 'data\coach_drafts\response-<request_id>.json'
```
## Demande créée par « Actualiser et analyser »

Une demande de la forme « Analyse la séance Garmin <activity_id> après mon
actualisation. » constitue le seul déclencheur d'une analyse sportive.

1. Suivre intégralement CODEX_AUTOMATION.md pour créer l'analyse structurée de
   l'activité correspondante.
2. Insister dans la réponse sur le respect de la séance prévue : type de séance,
   structure, zones cardiaques, allure au fil du temps et effet de l'altitude.
   Pour une endurance fondamentale, la vitesse brute n'est pas le critère
   principal : évaluer d'abord la maîtrise de la zone facile et la régularité.
3. Enregistrer ensuite une réponse claire à la demande avec plan_modifie à
   false.
4. Après une analyse réussie, suivre CODEX_WEEKLY.md. La semaine suivante ne
   doit être créée que si les trois séances du plan actif sont terminées.
5. Ne jamais lancer une synchronisation Garmin de sa propre initiative.
