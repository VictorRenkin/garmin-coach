# Audit du parcours utilisateur Motionest

> Référence auditée : `VictorRenkin/garmin-coach`, branche `main`, commit `99560d3`  
> Copie locale : `C:\Users\VICTOR.GDTECH-HP54\Documents\garmin-coach-github-website`  
> Vérification : **145 tests réussis**, 4 avertissements de dépendance.

## Parcours actuel réel

### 1. Création du compte Motionest

1. L’utilisateur ouvre `/` et choisit « Créer un compte ».
2. `POST /api/account/register` reçoit l’e-mail, le mot de passe, sa confirmation, l’acceptation des conditions et le jeton anti-robot.
3. Le backend normalise l’e-mail, contrôle son format, impose un mot de passe de 10 à 1024 caractères, vérifie l’unicité et exige l’acceptation des conditions.
4. Le mot de passe est dérivé avec `scrypt` et un sel aléatoire.
5. L’utilisateur est enregistré dans `data/users.json` avec `email_verified = false`.
6. Un token de vérification aléatoire est créé ; seul son hash SHA-256 est conservé.
7. Aucune session n’est créée à l’inscription.
8. L’e-mail est envoyé par SMTP si configuré, sinon écrit dans `data/outbox/`.

### 2. Vérification de l’e-mail

1. Le lien mène à `/verify-email?token=...`.
2. Le backend compare le hash du token et vérifie son expiration de 24 heures.
3. Il définit `email_verified = true` et `email_verified_at`.
4. Le hash et l’expiration sont supprimés : le token devient inutilisable.
5. L’utilisateur est redirigé vers la page de connexion ; aucune session automatique n’est créée.

### 3. Connexion Motionest

1. `POST /api/account/login` vérifie l’e-mail et le mot de passe.
2. Si l’e-mail n’est pas vérifié, la connexion est refusée en `403` et un nouveau lien est envoyé.
3. Sinon, `last_login` et `connections` sont mis à jour.
4. Une session signée est créée dans le cookie `motionest_session`.
5. L’utilisateur est redirigé directement vers `/dashboard`.

### 4. Première connexion réelle

Après une connexion Motionest, aucune connexion Garmin ni inspection Garmin n’est lancée. L’utilisateur arrive sur `/dashboard` même si Garmin n’est pas connecté et si l’onboarding vaut `not_started`.

L’onboarding visuel ne s’ouvre que pour le statut `needs_profile`. Ce statut est créé par `inspect_garmin_account()`, appelée dans l’ancien flux public `POST /api/login` qui utilise directement les identifiants Garmin.

### 5. Ancien flux Garmin

`POST /api/login` :

1. authentifie directement auprès de Garmin ;
2. crée éventuellement un enregistrement utilisateur ;
3. stocke les tokens Garmin ;
4. inspecte jusqu’à 100 activités et 100 entraînements ;
5. synchronise les activités ;
6. crée une session Motionest ;
7. redirige vers `/dashboard`.

Ce flux peut créer une session sans compte Motionest complet, sans mot de passe Motionest et sans vérification e-mail Motionest. Aucune interface active de la landing page ne semble toutefois l’appeler.

### 6. Onboarding existant

Quand Garmin a été inspecté, le statut devient `needs_profile` et un dialogue en trois étapes s’ouvre :

1. objectif : course, progression ou reprise, texte libre, distance et date facultatives ;
2. disponibilités : 2 à 5 séances et jours disponibles ;
3. contraintes : planning, fatigue/douleur/blessure et préférences.

Le formulaire complet est envoyé à `POST /api/profile` et stocké dans le `profile.json` propre à l’utilisateur. La machine d’état est :

```text
not_started → needs_profile → ready → plan_proposed → complete
                                      ↘ waiting_existing_plan
```

Le statut `complete` est atteint après acceptation et publication du plan Garmin.

### 7. Dashboard

Une session Motionest valide donne accès au dashboard et aux API athlète, indépendamment du statut de l’onboarding ou de Garmin. L’isolation des données entre utilisateurs est toutefois bien appliquée au moyen d’un identifiant opaque et d’un contexte de stockage par utilisateur.

## Matrice

| Fonction | État | Fichier(s) | Explication |
|---|---|---|---|
| Création d’un compte | EXISTE | `user_accounts.py`, `dashboard.py`, `landing.html` | Endpoint, interface et stockage présents. |
| Stockage utilisateur | EXISTE | `user_accounts.py`, `config.py` | Stockage JSON dans `data/users.json`. |
| Validation de l’e-mail | PARTIEL | `user_accounts.py` | Contrôles syntaxiques simples, pas de validateur RFC complet. |
| Confirmation des mots de passe | EXISTE | `dashboard.py`, `landing.html` | Vérifiée côté client et backend. |
| Unicité de l’e-mail | EXISTE | `user_accounts.py` | Comparaison après normalisation en minuscules. |
| Acceptation des conditions | EXISTE | `user_accounts.py`, `landing.html` | Version et date conservées. |
| Protection anti-robot | EXISTE | `account_security.py`, `landing.html` | Cloudflare Turnstile en production. |
| Politique de mot de passe | PARTIEL | `user_accounts.py` | Longueur 10–1024 uniquement. |
| Hash du mot de passe | EXISTE | `user_accounts.py` | `scrypt`, sel aléatoire, comparaison constante. |
| Session à l’inscription | MANQUANT | `dashboard.py` | Aucune session avant vérification et connexion. |
| Gestion des erreurs d’inscription | EXISTE | `dashboard.py`, `landing.html` | Erreurs de saisie, doublon, conditions et anti-robot. |
| Compte initialement non vérifié | EXISTE | `user_accounts.py` | `email_verified = false`. |
| Token e-mail aléatoire | EXISTE | `user_accounts.py` | `secrets.token_urlsafe(32)`. |
| Stockage sécurisé du token | EXISTE | `user_accounts.py` | Seul le hash SHA-256 est stocké. |
| Expiration du token e-mail | EXISTE | `user_accounts.py` | 24 heures. |
| Envoi de l’e-mail | PARTIEL | `account_security.py` | SMTP ou fichier local dans l’outbox. |
| Validation du token | EXISTE | `dashboard.py`, `user_accounts.py` | Route `/verify-email`. |
| Marquage e-mail vérifié | EXISTE | `user_accounts.py` | Champs `email_verified` et `email_verified_at`. |
| Token à usage unique | EXISTE | `user_accounts.py` | Hash supprimé après succès. |
| Renvoi de vérification | EXISTE | `dashboard.py`, `user_accounts.py` | Endpoint dédié et renvoi lors du login. |
| E-mail déjà vérifié | PARTIEL | `user_accounts.py` | Réponse générique, sans nouvel envoi. |
| Token invalide/expiré | EXISTE | `dashboard.py` | Redirection avec état d’erreur. |
| Blocage login sans vérification | PARTIEL | `dashboard.py` | Présent sur `/api/account/login`, contournable via `/api/login`. |
| Login Motionest | EXISTE | `dashboard.py`, `user_accounts.py` | Indépendant de Garmin. |
| Logout | PARTIEL | `dashboard.py` | Cookie supprimé, token signé non révoqué. |
| Cookie de session | EXISTE | `user_accounts.py` | `HttpOnly`, `SameSite=Strict`, `Path=/`. |
| Cookie `Secure` | PARTIEL | `user_accounts.py` | Seulement avec `MOTIONEST_ENV=production`. |
| Expiration de session | EXISTE | `user_accounts.py` | 8 heures ou 30 jours. |
| Rester connecté | EXISTE | `landing.html`, `user_accounts.py` | Session persistante de 30 jours. |
| Révocation des sessions | MANQUANT | `user_accounts.py` | Aucun registre de révocation. |
| Protection des routes | PARTIEL | `dashboard.py` | Session requise, étapes métier non vérifiées. |
| Mot de passe oublié | EXISTE | `landing.html`, `dashboard.py` | Réponse générique et token d’une heure. |
| Réinitialisation du mot de passe | EXISTE | `reset_password.html`, `user_accounts.py` | Token hashé, expirant et à usage unique. |
| Changement depuis un compte connecté | MANQUANT | — | Aucun parcours dédié. |
| Détection nouvel utilisateur | PARTIEL | `user_accounts.py`, `onboarding.py` | Données présentes, pas de routage adapté. |
| Détection onboarding terminé | EXISTE | `onboarding.py`, `user_accounts.py` | Statut détaillé et booléen récapitulatif. |
| Détection Garmin connecté | PARTIEL | `config.py`, `garmin.py` | Déductible des tokens, sans champ explicite. |
| Détection utilisateur revenant | PARTIEL | `user_accounts.py` | `last_login` et `connections`, sans routage associé. |
| Pages d’onboarding | EXISTE | `dashboard_v2.html` | Dialogue en trois étapes. |
| Endpoint onboarding | EXISTE | `dashboard.py` | `GET /api/onboarding`. |
| Enregistrement onboarding | EXISTE | `dashboard.py` | `POST /api/profile`. |
| Validation onboarding | EXISTE | `dashboard.py`, `dashboard_v2.html` | Validations client et backend. |
| Reprise d’un onboarding interrompu | MANQUANT | `dashboard_v2.html` | Aucune sauvegarde avant la soumission finale. |
| Fin d’onboarding | EXISTE | `onboarding.py`, `dashboard.py` | `complete` après acceptation/publication. |
| Connexion Garmin après onboarding | MANQUANT | — | Aucun écran ou endpoint dédié au nouveau parcours. |
| Authentification Garmin historique | EXISTE | `dashboard.py`, `garmin.py` | Ancien `/api/login`, avec MFA. |
| Stockage des tokens Garmin | EXISTE | `garmin.py`, `config.py` | Tokens isolés par utilisateur. |
| Première synchronisation | EXISTE | `dashboard.py`, `sync.py` | Réalisée dans l’ancien flux Garmin. |
| Routage métier complet | MANQUANT | `dashboard.py` | Seule la session est contrôlée. |
| Accès direct à `/dashboard` | PARTIEL | `dashboard.py` | Anonyme refusé ; connecté accepté sans autre condition. |
| Séparation utilisateurs | EXISTE | `config.py`, `dashboard.py` | Données et tokens isolés. |

## Modèle utilisateur actuel

Champs possibles observés dans `data/users.json` :

- `id`
- `email`
- `password_salt`
- `password_hash`
- `created_at`
- `connections`
- `activities_imported`
- `onboarding_complete`
- `email_verified`
- `email_verified_at`
- `email_verification_hash`
- `email_verification_expires`
- `terms_version`
- `terms_accepted_at`
- `last_login`
- `password_reset_hash`
- `password_reset_expires`
- `password_changed_at`

| Champ cible | Équivalent actuel |
|---|---|
| `email_verified` | Oui, champ exact. |
| `onboarding_status` | Partiel : `onboarding_complete` dans l’utilisateur et `status` dans `onboarding.json`. |
| `onboarding_version` | Non. |
| `garmin_connected` | Non ; déduit uniquement des fichiers de tokens. |
| `baseline_status` | Non. |
| `created_at` | Oui. |
| `last_login` | Oui. |

## Problèmes détectés

1. Le nouveau parcours Motionest ne contient aucune étape permettant de connecter Garmin.
2. L’onboarding ne démarre pas pour un nouveau compte Motionest : le statut reste `not_started`, alors que l’interface attend `needs_profile`.
3. Le dashboard est accessible avant l’onboarding et avant Garmin.
4. L’ancien endpoint public `/api/login` contourne le nouveau système de comptes et la vérification e-mail.
5. Deux parcours d’authentification concurrents coexistent.
6. Chaque connexion Motionest appelle `record_login(... onboarding_complete=False)` et remet le booléen utilisateur à `false`, même si le fichier d’onboarding est terminé.
7. Aucun champ ou endpoint explicite ne représente `garmin_connected`.
8. Le logout ne révoque pas le token signé côté serveur.
9. Une réinitialisation de mot de passe ne révoque pas les sessions existantes.
10. Le renvoi automatique des e-mails de vérification n’est pas limité.
11. Sans SMTP, aucun véritable e-mail n’est envoyé.
12. Un onboarding interrompu n’est pas repris.
13. Le statut d’onboarding est réparti entre `users.json` et `onboarding.json`, avec possibilité de divergence.
14. Les comptes hérités créés par l’ancien flux Garmin peuvent ne pas posséder de hash de mot de passe Motionest.

## Parcours cible vs actuel

| Cible | Actuel |
|---|---|
| `email_verified = false` → vérification | Appliqué sur `/api/account/login`, mais contournable par `/api/login`. |
| E-mail vérifié + onboarding incomplet → onboarding | Non imposé ; redirection vers `/dashboard`. |
| Onboarding terminé + Garmin absent → connexion Garmin | Aucun parcours dédié. |
| Garmin connecté → dashboard | Non imposé ; dashboard accessible avant Garmin. |
| Accès direct anonyme à `/dashboard` | Redirection correcte vers `/`. |
| Accès direct connecté avec onboarding incomplet | Autorisé. |
| Accès direct connecté sans Garmin | Autorisé. |

Le backend impose actuellement seulement :

```text
Pas de session → page d’accueil
Session valide → dashboard et API utilisateur
```

Il n’impose pas encore :

```text
vérification e-mail → onboarding → connexion Garmin → dashboard
```

## Fichiers concernés

- `garmin_coach/dashboard.py`
- `garmin_coach/user_accounts.py`
- `garmin_coach/onboarding.py`
- `garmin_coach/garmin.py`
- `garmin_coach/config.py`
- `garmin_coach/landing.html`
- `garmin_coach/dashboard_v2.html`
- `garmin_coach/reset_password.html`
- `tests/test_multitenant_security.py`
- `tests/test_onboarding.py`
- `tests/test_garmin.py`
- `tests/test_dashboard.py`

## Tests existants

### Inscription, e-mail, login et sessions

`tests/test_multitenant_security.py` couvre :

- séparation des espaces utilisateurs ;
- refus des API sans session ;
- séparation athlète/manager ;
- signature et falsification de session ;
- cookies de logout ;
- inscription Motionest indépendante de Garmin ;
- absence de session à l’inscription ;
- refus du login avant vérification ;
- hash du mot de passe ;
- doublons et mots de passe courts ;
- confirmation du mot de passe ;
- conditions et anti-robot ;
- tokens e-mail et reset à usage unique ;
- changement effectif du mot de passe ;
- isolation de deux utilisateurs ;
- erreurs Garmin et authentification manager.

### Onboarding

`tests/test_onboarding.py` couvre :

- inspection Garmin initiale ;
- absence de nouvelle inspection inutile ;
- résumé Garmin ;
- transitions d’état ;
- rejet d’un plan ;
- détection multisport, régularité et types d’entraînement.

### Garmin

`tests/test_garmin.py`, `tests/test_sync.py`, `tests/test_garmin_workouts.py` et `tests/test_hr_workouts.py` couvrent :

- authentification et stockage des tokens ;
- réutilisation des tokens ;
- erreurs réseau, identifiants, MFA et limitation Garmin ;
- synchronisation idempotente ;
- construction et publication des séances.

### Dashboard

`tests/test_dashboard.py` couvre le profil, les endpoints sportifs, l’historique, les détails, les séances et plusieurs comportements visuels.

Résultat global : **145 tests réussis**.

## Tests manquants

- Parcours complet inscription → vérification → login → onboarding → Garmin → dashboard.
- Redirections selon les statuts `not_started`, `needs_profile`, `ready` et `complete`.
- Refus du dashboard sans Garmin.
- Connexion Garmin depuis une session Motionest existante.
- Vérification que `/api/login` ne contourne pas les comptes Motionest.
- Conservation de `onboarding_complete` après reconnexion.
- Reprise d’un onboarding interrompu.
- Expiration réelle des tokens de vérification et de reset.
- Limitation des renvois d’e-mails.
- Révocation des sessions au logout et après reset du mot de passe.
- Cookie `Secure` en production.
- Comportement de production sans SMTP.
- Gestion des comptes hérités créés par l’ancien flux Garmin.
- Accès direct aux routes en sautant une étape.

## Recommandation pour la PREMIÈRE modification

Créer une seule fonction backend centralisée et testée qui calcule l’étape actuelle d’un utilisateur authentifié :

```text
verify_email
onboarding
connect_garmin
dashboard
```

Cette fonction doit seulement lire :

- `email_verified` ;
- le statut réel de `onboarding.json` ;
- la présence valide des deux tokens Garmin.

Elle constituera une fondation isolée avant d’appliquer les redirections aux routes existantes.

---

**Aucune correction ni fonctionnalité n’a été implémentée dans le cadre de cet audit.**
