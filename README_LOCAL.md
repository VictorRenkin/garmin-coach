# Tester Motionest entièrement en local

Ce mode permet de tester le parcours de première utilisation sans service
anti-robot et sans cliquer sur un e-mail de confirmation. Il conserve malgré
tout les contrôles utiles : adresse unique, mot de passe, confirmation du mot
de passe, acceptation des conditions et distinction entre nouveau compte et
compte existant.

Le contournement n'est actif que si la variable
`MOTIONEST_LOCAL_AUTH_BYPASS` est activée **et** si l'application est appelée
depuis `localhost` ou `127.0.0.1`.

## Commandes PowerShell

Depuis le dossier du projet :

```powershell
cd C:\Users\VICTOR.GDTECH-HP54\Documents\garmin-coach-github-website
$env:MOTIONEST_ENV = "development"
$env:MOTIONEST_LOCAL_AUTH_BYPASS = "true"
& "C:\Users\VICTOR.GDTECH-HP54\Documents\garmin-coach-main\garmin-coach-main\.venv\Scripts\python.exe" -m garmin_coach.dashboard --port 8766 --no-browser
```

Ouvre ensuite [http://127.0.0.1:8766](http://127.0.0.1:8766).

Si le projet possède sa propre `.venv`, la dernière commande peut simplement
être remplacée par :

```powershell
.\.venv\Scripts\python.exe -m garmin_coach.dashboard --port 8766 --no-browser
```

Arrête le serveur avec `Ctrl+C`.

## Parcours obtenu

- **Nouvelle adresse** : le compte est créé, marqué vérifié localement, une
  session est ouverte et Motionest affiche l'onboarding.
- **Adresse déjà inscrite** : une nouvelle inscription est refusée avec le
  message indiquant que le compte existe déjà. Utilise alors l'onglet de
  connexion.
- **Compte local existant non vérifié** : une connexion avec le bon mot de
  passe le valide localement, ouvre la session et reprend l'étape réelle du
  parcours.
- **Compte ayant déjà avancé** : la connexion conserve son onboarding et le
  dirige vers l'étape calculée (onboarding, connexion Garmin ou dashboard).
- Aucun e-mail de confirmation et aucun challenge Turnstile ne sont envoyés
  dans ce mode.

Les règles de mot de passe, l'acceptation des conditions et l'unicité de
l'adresse restent actives. Les données sont enregistrées dans les mêmes
espaces locaux par utilisateur que dans le parcours normal.

## Revenir au parcours normal

Dans PowerShell, arrête le serveur puis exécute :

```powershell
Remove-Item Env:MOTIONEST_LOCAL_AUTH_BYPASS -ErrorAction SilentlyContinue
```

Relance ensuite Motionest normalement. Une variable définie avec `$env:` ne
concerne que la fenêtre PowerShell actuelle et disparaît lorsqu'elle est
fermée.

> Ce mode est réservé aux essais locaux. Ne configure jamais
> `MOTIONEST_LOCAL_AUTH_BYPASS=true` sur un serveur public.
