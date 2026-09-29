# Plan de développement Motionest

Ce document décrit l'ordre de développement du parcours utilisateur. Chaque
étape doit rester petite, testée et publiée sur la branche `main` avant de
commencer la suivante.

## Principes

- Le compte Motionest reste distinct du compte Garmin.
- Le backend reste la source de vérité du parcours utilisateur.
- Les données et tokens Garmin sont toujours isolés par utilisateur.
- Le statut réel de `onboarding.json` prévaut sur le booléen récapitulatif du compte.
- Chaque modification doit conserver les tests existants et ajouter ses tests de non-régression.

## Parcours cible

```text
Création du compte
→ vérification de l'e-mail
→ connexion Motionest
→ onboarding
→ connexion Garmin
→ première synchronisation
→ dashboard
```

## Étapes de développement

### 1. Déterminer l'étape utilisateur — terminé

`determine_user_stage(user_id)` retourne `verify_email`, `onboarding`,
`connect_garmin` ou `dashboard` à partir du compte, du statut réel de
l'onboarding et des tokens Garmin propres à l'utilisateur.

### 2. Router après une connexion Motionest — terminé

Utiliser l'étape centralisée uniquement après `POST /api/account/login`, sans
généraliser encore la protection à toutes les routes.

### 3. Construire la connexion Garmin moderne — à faire

Créer un parcours Garmin rattaché à une session Motionest existante. Ne pas
réutiliser l'ancien `/api/login` sans audit spécifique.

### 4. Première synchronisation contrôlée — à faire

Déclencher l'inspection et la synchronisation après une connexion Garmin
réussie, avec erreurs explicites et isolation utilisateur.

### 5. Imposer le parcours aux routes — à faire

Appliquer progressivement la décision backend aux routes sensibles, avec des
tests d'accès direct pour chaque étape.

### 6. Fonctionnalités produit suivantes — à faire

La baseline sportive, Garmin Health, les statistiques et la refonte du
dashboard restent hors périmètre tant que le parcours n'est pas stabilisé.

## Référence

Voir `AUDIT_PARCOURS_UTILISATEUR_MOTIONEST.md` pour l'état initial du code et
les écarts observés.
