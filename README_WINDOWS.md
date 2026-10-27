# Motionest — Installation sur Windows

[← Présentation du projet](README.md) · [Installation Mac](README_MACOS.md)

Ce guide permet d'utiliser Motionest sur **ton propre ordinateur**, avec ton
compte Garmin et ta clé API. Aucun fichier ni compte du créateur du projet
n'est nécessaire. L'application tourne sur le PC et s'ouvre dans le navigateur.

## 1. Préparer le PC (une seule fois)

Il faut Windows 10/11, Git et Python 3.11 ou plus récent. Si Git et Python
sont déjà installés, passe à l'étape 2.

Sinon, ouvre **PowerShell** et lance ces commandes :

```powershell
winget install --id Git.Git --exact
winget install --id Python.Python.3.12 --exact
```

Ferme puis rouvre PowerShell pour que les commandes soient reconnues.
Si `winget` est indisponible, installe Git depuis <https://git-scm.com/downloads/win>
et Python depuis <https://www.python.org/downloads/windows/> avec le lanceur
Python (`py`).

## 2. Télécharger et installer Motionest

Copie ces commandes dans PowerShell, une par une. Si une commande échoue,
corrige l'erreur avant de poursuivre.

```powershell
cd $HOME
git clone https://github.com/VictorRenkin/garmin-coach.git
cd garmin-coach
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Si tu as téléchargé le dépôt avec **Code → Download ZIP**, extrais d'abord
l'archive. Ouvre le dossier qui contient `requirements.txt`, puis fais un clic
droit dans ce dossier → **Ouvrir dans le Terminal**. Lance seulement les deux
dernières commandes ci-dessus : la création de `.venv` et l'installation.

Il n'est pas nécessaire d'activer l'environnement Python ni de modifier la
politique d'exécution PowerShell.

## 3. Mettre ta clé API

Dans le dossier du projet, lance :

```powershell
.\configurer-ia.cmd
```

Le programme te demande :

1. Le fournisseur : tape par exemple `nvidia`, `openai`, `groq` ou `openrouter`.
2. **Ta propre clé API**, créée auprès de ce fournisseur. La saisie est masquée :
   rien ne s'affiche pendant que tu colles la clé, c'est normal. Valide avec Entrée.
3. Le nom du modèle : saisis un modèle disponible sur ton compte fournisseur.
   Entrée conserve la valeur proposée, si ce modèle est disponible pour toi.

La clé est enregistrée dans le fichier `.env` de ton PC, ignoré par Git.
La disponibilité des modèles, les quotas et les éventuels frais dépendent du
fournisseur et de ton compte. Ne partage pas ce fichier.

Tu peux relancer cette même commande pour remplacer la clé ou le fournisseur.
Si Motionest tourne déjà, arrête-le avec `Ctrl+C`, puis relance-le après la
configuration.

## 4. Ouvrir la page et créer ton compte

Pour cette utilisation locale, lance les commandes suivantes dans PowerShell :

```powershell
$env:MOTIONEST_ENV = "development"
$env:MOTIONEST_LOCAL_AUTH_BYPASS = "true"
.\ouvrir-dashboard.cmd
```

Le navigateur s'ouvre automatiquement sur **<http://127.0.0.1:8765>**.
Garde la fenêtre du terminal ouverte pendant l'utilisation.

Sur la page :

1. Crée ton compte Motionest avec ton adresse et un mot de passe.
2. Renseigne ton profil et ton objectif en suivant les étapes affichées.
3. Connecte **ton compte Garmin depuis la page**, avec le code de vérification
   Garmin s'il est demandé.
4. Suis l'import initial, puis utilise **Actualiser et analyser** pour les
   nouvelles courses après synchronisation de ta montre avec Garmin Connect.

Le mode local ci-dessus évite le service anti-robot et la confirmation par
e-mail. Il conserve le compte et son mot de passe, et ne s'applique qu'aux
accès depuis localhost. Il est réservé à ton PC, pas à un serveur public.
Les variables définies ici disparaissent à la fermeture de PowerShell.

## Les prochaines fois

Ouvre PowerShell et copie :

```powershell
cd "$HOME\garmin-coach"
$env:MOTIONEST_ENV = "development"
$env:MOTIONEST_LOCAL_AUTH_BYPASS = "true"
.\ouvrir-dashboard.cmd
```

Si tu as utilisé un ZIP ou choisi un autre dossier, remplace le chemin de la
première commande par celui de ton installation.

Tu retrouves ton compte et tes données. Inutile de réinstaller ou de ressaisir
la clé API à chaque démarrage. Pour arrêter Motionest, appuie sur `Ctrl+C`
dans le terminal.

## En cas de problème

| Message ou situation | Solution |
| --- | --- |
| `git` ou `py` non reconnu | Termine l'installation du prérequis, puis rouvre PowerShell. Vérifie `py -3 --version` : il faut Python 3.11 minimum. |
| Environnement Python introuvable | Reviens dans le dossier contenant `requirements.txt` et refais les deux dernières commandes de l'étape 2. |
| Le navigateur ne s'ouvre pas | Laisse le serveur démarré et ouvre <http://127.0.0.1:8765> manuellement. |
| Le port 8765 est déjà utilisé | Arrête l'autre instance de Motionest, ou lance `.\.venv\Scripts\python.exe -m garmin_coach.dashboard --port 8766` et ouvre <http://127.0.0.1:8766>. |
| Le coach signale une erreur de clé, de modèle ou de quota | Relance `.\configurer-ia.cmd`, vérifie les informations auprès de ton fournisseur puis redémarre l'application. |

Les fichiers personnels restent dans `.env`, `.secrets/` et `data/`, exclus
de Git. Chaque personne installe sa propre copie du dépôt sur son PC.
