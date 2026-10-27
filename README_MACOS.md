# Motionest — Installation sur Mac

[← Présentation du projet](README.md) · [Installation Windows](README_WINDOWS.md)

Installe Motionest sur ton Mac, configure ta propre clé API et connecte ton
compte Garmin. L'application fonctionne localement et s'ouvre dans ton navigateur.
Tu n'as pas besoin de Codex pour utiliser le coach avec une clé API.

## 1. Préparer le Mac

Ouvre **Terminal** depuis Applications → Utilitaires, ou cherche « Terminal »
avec Spotlight (`⌘ + Espace`). Toutes les commandes de ce guide se lancent dedans.

Il faut **Python 3.11 ou plus récent** et Git. Vérifie leur présence :

```bash
python3 --version
git --version
```

Si Python est absent ou trop ancien, installe une version récente de Python 3
avec l'installateur macOS de [python.org](https://www.python.org/downloads/macos/).
Ferme et rouvre Terminal, puis vérifie à nouveau la version.

Si Git est absent, lance :

```bash
xcode-select --install
```

Accepte l'installation des outils de ligne de commande dans la fenêtre macOS
et attends qu'elle soit terminée. Si macOS propose déjà cette installation
avec `git --version`, termine simplement cette étape.

## 2. Télécharger le projet

Copie les commandes une par une. Si l'une échoue, corrige l'erreur avant de
passer à la suivante.

```bash
cd "$HOME"
git clone https://github.com/VictorRenkin/garmin-coach.git
cd garmin-coach
```

**Déjà téléchargé avec Code → Download ZIP sur GitHub ?** Double-clique sur le
ZIP pour l'extraire. Dans Terminal, tape `cd ` (avec un espace), glisse le
dossier extrait depuis Finder dans Terminal, puis appuie sur Entrée.
Ce dossier doit contenir `requirements.txt` et `garmin_coach`.
Passe ensuite à l'étape suivante sans refaire `git clone`.

## 3. Installer l'application

Depuis le dossier du projet :

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Attends la fin de l'installation. L'environnement `.venv` contient les
dépendances du projet ; tu n'as pas besoin de l'activer ni d'utiliser `sudo`.

## 4. Enregistrer ta clé API

```bash
./.venv/bin/python -m garmin_coach.setup_ai
```

Le programme demande :

1. **Le fournisseur** : tape par exemple `nvidia`, `openai`, `groq` ou `openrouter`.
2. **Ta clé API** : colle la clé créée sur ton compte chez ce fournisseur,
   puis appuie sur Entrée. La saisie est masquée, donc aucun caractère ne s'affiche.
3. **Le modèle** : saisis le nom d'un modèle disponible sur ton compte.
   Entrée conserve le modèle proposé, s'il est disponible pour toi.

La clé est conservée dans `.env`, un fichier local ignoré par Git. Ne le partage
pas. Les modèles disponibles, les quotas et les éventuels frais dépendent de
ton fournisseur. Tu peux relancer la commande pour changer la configuration.

## 5. Ouvrir Motionest

Pour utiliser l'application sur ton Mac sans configurer de service d'e-mail
ni d'anti-robot, lance :

```bash
MOTIONEST_ENV=development MOTIONEST_LOCAL_AUTH_BYPASS=true ./.venv/bin/python -m garmin_coach.dashboard
```

Ton navigateur s'ouvre automatiquement sur **[http://127.0.0.1:8765](http://127.0.0.1:8765)**.
Si ce n'est pas le cas, ouvre ce lien toi-même. Garde Terminal ouvert pendant
l'utilisation : il fait tourner l'application.

Sur la page :

1. Crée ton compte Motionest avec ton adresse et un mot de passe.
2. Suis les étapes pour compléter ton profil et ton objectif.
3. Connecte ton propre compte Garmin **depuis la page**, puis saisis l'éventuel
   code de vérification Garmin.
4. Suis l'import initial. Pour les prochaines sorties, synchronise ta montre
   avec Garmin Connect, puis utilise **Actualiser et analyser**.

Le mode local valide le compte sans e-mail de confirmation et sans challenge
anti-robot. Le mot de passe reste nécessaire. Ce mode s'applique uniquement
aux accès depuis localhost et est réservé à ton ordinateur.

## Relancer l'application les prochaines fois

Ouvre Terminal et lance :

```bash
cd "$HOME/garmin-coach"
MOTIONEST_ENV=development MOTIONEST_LOCAL_AUTH_BYPASS=true ./.venv/bin/python -m garmin_coach.dashboard
```

Si tu as téléchargé le ZIP ou choisi un autre emplacement, remplace le chemin
de la première commande par celui de ton dossier.

Ton compte, tes données et ta clé sont conservés. Tu n'as pas besoin de refaire
l'installation. Pour arrêter l'application, appuie sur **Ctrl + C** dans Terminal.

## Modifier la clé API

Arrête l'application avec `Ctrl + C`, puis lance ces commandes depuis son dossier :

```bash
./.venv/bin/python -m garmin_coach.setup_ai
MOTIONEST_ENV=development MOTIONEST_LOCAL_AUTH_BYPASS=true ./.venv/bin/python -m garmin_coach.dashboard
```

## En cas de problème

| Situation | Que faire |
| --- | --- |
| `python3` introuvable ou version inférieure à 3.11 | Installe Python depuis le lien de l'étape 1 et rouvre Terminal. |
| `requirements.txt` introuvable | Reviens dans le dossier extrait ou cloné qui contient ce fichier. |
| `.venv/bin/python` introuvable | Refais les deux commandes de l'étape 3 dans le dossier du projet. |
| La page ne répond pas | Vérifie que Terminal est toujours ouvert, que le serveur tourne et qu'aucune erreur n'y est affichée. |
| Port 8765 déjà utilisé | Arrête l'autre instance, ou ajoute `--port 8766` à la commande de démarrage et ouvre [http://127.0.0.1:8766](http://127.0.0.1:8766). |
| Erreur de clé, de modèle ou de quota | Vérifie ton compte fournisseur, relance la configuration de l'IA puis redémarre Motionest. |

Chaque utilisateur installe sa propre copie du projet. Les fichiers `.env`,
`.secrets/` et `data/` contiennent la configuration et les données personnelles
et sont exclus de Git.
