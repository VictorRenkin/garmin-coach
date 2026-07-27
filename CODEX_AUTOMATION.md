# Analyse Garmin manuelle avec ChatGPT/Codex

Cette procédure n'est jamais lancée pour interroger Garmin périodiquement. Elle
n'utilise aucune clé API OpenAI et intervient uniquement après un clic explicite
sur « Actualiser et analyser » dans le tableau de bord.

## Procédure obligatoire

1. Ne pas lancer garmin_coach.cli sync : le bouton du tableau de bord a déjà
   effectué la synchronisation demandée par l'utilisateur.
2. Lancer la commande suivante :

   & '.\.venv\Scripts\python.exe' -m garmin_coach.codex_bridge pending

3. Si courses est vide, terminer sans modifier de fichier.
4. Pour chaque course demandée, suivre strictement les instructions, le schéma
   et le contexte fournis. Ne lire ni jetons Garmin, ni .env, ni coordonnées ou
   données GPS brutes.
5. L'analyse doit comparer la séance réalisée à la séance planifiée : structure,
   type (notamment endurance fondamentale), zones cardiaques, régularité de
   l'allure selon le temps et influence de l'altitude. Présenter clairement ce
   qui a été respecté, les améliorations et les limites des données.
6. Écrire l'objet JSON dans data/codex_drafts/<activity_id>.json, puis valider
   avec garmin_coach.codex_bridge save-analysis.
7. Ne jamais remplacer une analyse existante et ne poser aucun diagnostic.
