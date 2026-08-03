# Garmin Coach

### Tes courses Garmin deviennent un plan d’entraînement qui évolue avec toi.

Garmin Coach est une application locale pour les coureurs qui veulent comprendre leurs séances et savoir quoi courir ensuite. Elle rassemble ton historique Garmin, détaille chaque sortie, fait analyser tes données par un coach IA et prépare la semaine suivante directement dans Garmin Connect.

![Aperçu du tableau de bord Garmin Coach](docs/assets/dashboard.png)

> Un seul tableau de bord pour passer de « voilà ma course » à « voilà ce que je dois faire maintenant ».

## Pourquoi l’utiliser ?

Garmin Connect collecte beaucoup de données. Garmin Coach les transforme en décisions utiles :

- **Comprendre chaque sortie** : allure, fréquence cardiaque, dénivelé, cadence, foulée et détail kilomètre par kilomètre.
- **Recevoir un retour concret** : le coach compare la séance réalisée à l’objectif prévu et à l’historique récent.
- **Suivre un vrai objectif** : 5 km, semi-marathon ou autre distance, avec date cible et fréquence d’entraînement personnalisables.
- **Savoir quoi courir ensuite** : la semaine suivante est structurée en échauffement, bloc principal, zones cardio et récupération.
- **Retrouver ses séances sur Garmin** : les entraînements sont créés puis programmés dans le calendrier Garmin Connect.
- **Adapter la semaine sans perdre le fil** : reporte une séance ou indique qu’elle n’a pas été faite ; le plan garde un état fidèle à la réalité.
- **Échanger avec son coach** : demande une adaptation en langage naturel depuis le tableau de bord.

## Comment ça marche ?

```text
Montre Garmin → Garmin Connect → Synchronisation locale → Analyse IA
                                                        ↓
Tableau de bord ← Entraînements Garmin ← Nouveau plan hebdomadaire
```

L’application fonctionne sur ton ordinateur et s’ouvre dans le navigateur. Le bouton **Actualiser et analyser** importe les nouvelles courses à la demande : aucune synchronisation cachée ne démarre au lancement.

## Démarrage rapide sous Windows

### 1. Installer

Prérequis : **Python 3.11 ou plus récent**, un compte Garmin Connect et Windows 10/11.

```powershell
git clone https://github.com/VictorRenkin/garmin-coach.git
cd garmin-coach
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Connecter Garmin

```powershell
.\connexion-garmin.cmd
```

L’adresse, le mot de passe et l’éventuel code de vérification sont demandés de manière interactive. Le mot de passe n’a pas besoin d’être enregistré. Les jetons de session restent dans `.secrets/garmin_tokens/`, hors de Git.

### 3. Ouvrir l’application

```powershell
.\ouvrir-dashboard.cmd
```

Le navigateur s’ouvre sur [http://127.0.0.1:8765](http://127.0.0.1:8765). Clique sur **Actualiser et analyser** pour importer tes courses.

À ce stade, le suivi, les statistiques et les actions Garmin fonctionnent déjà sans OpenAI.

## Ce qui fonctionne selon ta configuration

| Fonction | Sans API OpenAI | Avec API OpenAI |
| --- | :---: | :---: |
| Importer et consulter les courses Garmin | ✓ | ✓ |
| Afficher les graphiques, zones et tours | ✓ | ✓ |
| Gérer l’objectif et reporter une séance | ✓ | ✓ |
| Discuter avec le coach dans le tableau de bord | — | ✓ |
| Demander une adaptation en langage naturel | — | ✓ |

Sans API, tu peux également ouvrir le projet dans Codex et discuter avec l’assistant depuis l’application Codex. Pour utiliser le coach directement dans le tableau de bord, il faut une clé API OpenAI. L’API est facultative, payante à l’usage et facturée séparément d’un abonnement ChatGPT ou Codex.

### Activer le coach dans le tableau de bord

Crée une clé depuis [OpenAI Platform](https://platform.openai.com/api-keys), puis lance :

```powershell
.\configurer-ia.cmd
```

La clé est stockée dans le fichier local `.env`, ignoré par Git. Ne publie jamais ce fichier et n’enregistre jamais tes identifiants ou ton mot de passe ChatGPT dans l’application. Ensuite, utilise simplement **Actualiser et analyser** dans l’interface.

## Ce que tu retrouves dans le tableau de bord

| Espace | Ce qu’il apporte |
| --- | --- |
| Objectif | Distance cible, date de course, fréquence hebdomadaire et progression |
| Dernière sortie | Distance, allure, dénivelé, cadence et longueur de foulée |
| Courbes | Évolution de l’allure, du cardio et des zones d’effort |
| Détail par kilomètre | Tours, FC moyenne/maximale, cadence, foulée et dénivelé |
| Analyse du coach | Verdict, points forts, points de vigilance et prochaine priorité |
| Plan de la semaine | Séances structurées, zones cardio, statut et report de date |
| Historique | Toutes les courses importées, avec accès à l’activité Garmin |
| Conversation | Questions de ressenti, conseils et adaptations à confirmer dans le même fil |

## Tes données restent sous ton contrôle

- Les identifiants, jetons Garmin, activités, analyses et rapports sont exclus du dépôt.
- Le serveur écoute uniquement sur `127.0.0.1` par défaut : le tableau de bord n’est pas exposé au réseau local.
- Le coach IA reçoit uniquement le contexte sportif nécessaire pour fournir une réponse personnalisée.
- Une proposition du coach ne modifie l’objectif ou Garmin qu’après confirmation dans le chat.
- Rien ne remplace l’avis d’un professionnel de santé : les recommandations sont sportives, pas médicales.

## Commandes utiles

```powershell
# Tester la connexion Garmin
.\.venv\Scripts\python.exe -m garmin_coach.cli login

# Importer les nouvelles courses sans passer par le tableau de bord
.\.venv\Scripts\python.exe -m garmin_coach.cli sync

# Lancer le tableau de bord sans ouvrir automatiquement le navigateur
.\.venv\Scripts\python.exe -m garmin_coach.dashboard --no-browser
```

## Limites actuelles

- Le projet cible aujourd’hui Windows et la course à pied.
- La montre doit avoir synchronisé l’activité avec Garmin Connect avant l’import.
- L’intégration s’appuie sur une interface Garmin Connect non officielle et peut nécessiter une adaptation si Garmin la modifie.
- L’application est conçue pour un usage personnel et local ; elle ne fournit ni comptes multiples ni hébergement cloud.

---

**Branche ta Garmin. Lance le dashboard. Transforme ta prochaine course en prochaine étape.**
