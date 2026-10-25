# Validation IA — lecture et intentions des séances V2

Campagne unique autorisée sur le candidat `2211d82189c3c8f9853e8a90304404d89bbb8769`, le 25 octobre 2026. Aucun appel supplémentaire ni retry. `main` non modifié par cet agent.

## Méthode et périmètre

Le script `scripts/validate_coach_sessions_v2.py` copie uniquement le code du candidat vers un `TemporaryDirectory` avant les imports applicatifs. Les données/stores utilisés sont synthétiques et temporaires. La configuration du fournisseur est chargée par `load_dotenv` depuis le checkout principal explicitement autorisé, sans afficher ni exporter la clé. Les lecteurs calendrier V2, les instructions et le schéma `CoachResponse` sont ceux du candidat.

Le fournisseur est appelé directement avec `structured_completion`, `quality=deep`, `max_retries=0`, timeout 60 s et sortie bornée 900 tokens. Ce choix teste le **modèle**, sans compter comme appel IA une réponse déterministe locale. Le modèle réellement enregistré par les six lignes d'usage est **`gpt-5.6-sol`**, identique au modèle configuré. Aucun routage vers une variante économique pendant cette campagne.

Les fixtures 3C existantes construisent le calendrier synthétique. Le scénario du soir comporte deux séances, `morning` à 08 h et `evening` à 18 h ; son historique conversationnel contient la demande précédente de raccourcir à 25 minutes, puis la question matin/soir. Les autres scénarios ciblent la séance `evening`, mardi 27 octobre 2026 à 18 h, 35 minutes, phases 5/25/5 et intensité facile. Acceptation absente et livraison indisponible sont explicitement projetées. Aucun compte, conversation ou historique personnel n'est chargé.

## Résultats relus fonctionnellement

| Scénario | Résultat réellement observé |
|---|---|
| « J'ai quoi comme séances cette semaine ? » | Cite mardi 27 octobre 18 h, 35 min, phases 5/25/5 et intensité facile ; acceptation non enregistrée et livraison indisponible explicites ; aucun entraînement réalisé inventé |
| « Raccourcis ma séance de mardi à 25 minutes. » | `session_action_v2.action=modify`, `session_id=evening`, `target_duration_minutes=25` ; annonce un aperçu à confirmer, nouvelle acceptation nécessaire et absence de publication automatique |
| « Déplace-la à jeudi. » | `action=move`, même identité `evening`, `target_date=2026-10-29`, `target_time=18:00` ; aucune modification immédiate prétendue |
| « Supprime ma séance de mardi. » | Refus explicite du retrait V2 non disponible ; distingue refus de la séance et suppression ; aucune intention/action de suppression fabriquée |
| « Je parle de celle du soir, pas du matin. » | Exploite la demande antérieure : `modify`, identité `evening`, durée 25 ; cite 18 h et un brouillon à confirmer, sans confondre avec le matin |
| « Annule, finalement. » | Aucune nouvelle intention ni action historique ; répond qu'il annule le brouillon, séance inchangée et aucune publication |

**6/6 succès techniques**, **6/6 résultats satisfaisants pour la lecture et la reconnaissance des intentions au niveau fournisseur**. Tous portent `action=none` pour le chemin historique, `plan_modifie=false` et des messages conformes au budget de 3 000 caractères. Les contrôles automatiques sont complétés par cette relecture des champs durée, date et identité ; ils ne suffisent pas seuls à qualifier le comportement fonctionnel.

Nuance obligatoire sur l'annulation : la phrase du modèle « j'annule le brouillon » n'a pas d'effet dans ce probe direct. Aucun brouillon persistant réel n'a été préparé puis annulé par ce script. Cette réponse est satisfaisante comme intention sans nouvelle modification ; sa vérité opérationnelle dépend du handler local/déterministe et doit être établie par les tests/UI de l'orchestrateur. Elle ne constitue pas une preuve d'annulation de données.

## Consommation et preuves

- Six appels initiaux, zéro retry et aucun appel additionnel.
- **39 283 tokens d'entrée + 996 tokens de sortie = 40 279 tokens** au total.
- Modèle réellement journalisé six fois : `gpt-5.6-sol`, fournisseur `openai`.
- État synthétique inchangé avant/après, hors journal d'usage attendu. Aucun appel Garmin, aucune confirmation de brouillon, mutation locale ou publication effectuée.
- Rapport détaillé ignoré : `reports/coach-v2-real-ai.json` dans le worktree candidat `motionest-coach-v2-candidate/garmin-coach-main`. Il contient les réponses structurées, contrôles, tailles de payload et six entrées d'usage ; aucune clé ni configuration sensible n'y est exportée.

Commande utilisée : Python du venv principal, script de la branche A, options `--candidate <worktree candidat> --config-root <checkout principal autorisé> --real --output reports/coach-v2-real-ai.json`. Le script reste en dry-run par défaut ; `--real` exige une racine de configuration explicite. Le dry-run antérieur préparait six cas avec zéro appel et zéro token.

Limites : pas une preuve navigateur, pas une validation de confirmation, annulation ou retrait effectif, pas de contrôle d'exécution des blocs après modification par cette campagne. Les tests synthétiques des moteurs/handlers et les parcours UI complémentaires restent nécessaires. Aucun fichier mobile ou donnée réelle consulté, aucune modification du candidat applicatif, de `main`, aucun push.
