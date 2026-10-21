# Pilote : configuration locale accompagnée

Référence : livraison `f44011484977019394f2462d0af4ef160501438f`.
Branche documentaire `codex/motionest-pilot-local-config`. Aucun serveur lancé,
secret réel lu, configuration modifiée ou service contacté pendant cette rédaction.
Ce guide prépare un démarrage existant, sans autoriser le lancement du pilote.

## Préconditions et paramètres observés

Réutiliser `README.md`, `README_LOCAL.md`, `.env.example`,
`PILOTE_WEB_PREPARATION.md` et `PILOTE_RESOLUTION_GARMIN_INCERTAINE.md`.
`tool-library/USAGE.md` et métadonnées locales ont été consultés ; les résultats
de recherche sécurité génériques visaient d'autres stacks. Aucun outil d'audit
ou skill d'exploitation n'était nécessaire pour cette lecture ciblée du code.

| Élément | Réglage/code existant | Vérification opérateur avant essai |
|---|---|---|
| Écoute | `dashboard.build_parser` : host `127.0.0.1`, port8765, option `--no-browser` | Utiliser explicitement loopback ; choisir un port libre et une seule instance |
| URL des liens | `MOTIONEST_PUBLIC_URL`, défaut `http://127.0.0.1:8765` | Même hôte et port que l'essai, notamment si8766 ; ne pas rendre les liens publics |
| Mode | `account_security.production_mode` : seule valeur `production` active ce mode | Local HTTP : development ; ne pas confondre ce réglage avec une homologation de sécurité |
| Session | `user_accounts.session_cookie` : HttpOnly, SameSite=Strict, Path=/ ; Secure en production | Même nom d'hôte utilisé tout au long du parcours ; aucun cookie copié dans un ticket |
| Durée | Session signée : 8h par défaut, option remember30jours | Compte du participant attendu et déconnexion en fin d'essai, surtout navigateur partagé |
| Auth locale explicite | `MOTIONEST_LOCAL_AUTH_BYPASS` vrai et client/Host loopback | Choix déclaré réservé au test local ; jamais pour justifier une identité vérifiée par e-mail réel |
| E-mail | Sans SMTP_HOST : fichiers `data/outbox`; sinon SMTP+STARTTLS | Choisir local outbox pour essai sans service ; liens de vérification/réinitialisation confidentiels |
| Anti-robot | Sans secret en développement : jeton `development-pass`; bypass local explicite accepté | Pas de preuve Cloudflare réelle ; ne pas configurer des clés réelles pour cette seule vérification |
| IA | `config.load_settings` choisit fournisseur/modèle/baseURL/clé ; aucune clé requise pour lancer le serveur seul | Chat réel à autoriser séparément, fournisseur et budget connus ; aucun test payant implicite |
| Garmin | Découverte sans source et connexion ultérieure existantes | Choisir « Continuer sans Garmin », ne pas saisir d'identifiants sportifs pour le pilote initial |

Attention à la priorité : `config.load_settings` utilise
`load_dotenv(ROOT_DIR / ".env", override=True)`. Les valeurs d'un fichier local
peuvent remplacer celles du processus lors d'un chargement de configuration.
L'opérateur doit garantir leur cohérence sans afficher/coller le fichier ou les
secrets. Ce guide n'a pas inspecté ce fichier et ne certifie aucun réglage effectif.
Ne pas supposer qu'une commande PowerShell suffit à neutraliser un ancien réglage.

## Deux parcours locaux distincts

**Normal en développement** : bypass absent, compte Motionest, mot de passe et
confirmation d'adresse conservés. Sans SMTP, la confirmation se fait par le lien
local confidentiel de l'outbox ou le lien de développement renvoyé par le parcours
prévu ; ce n'est pas un e-mail livré à son destinataire. La protection humaine
développement n'est pas la preuve d'un challenge Cloudflare réel.

**Test local explicite** (`README_LOCAL.md`) : development et bypass activé dans
le processus cohérent avec sa configuration. Nouvelle inscription vérifiée
localement et session ouverte ; compte existant non vérifié confirmé localement
après bon mot de passe. Les règles de mot de passe, unicité et conditions restent
actives. Ne pas prendre ces comptes comme preuve de vérification d'identité réelle.
Ce mode n'envoie pas de confirmation e-mail et dispense le challenge humain local.
Il ne désactive ni l'authentification ni l'isolation des données.

## Démarrage existant, à exécuter seulement après décision de lancement

Préparer la livraison retenue, Python et ses dépendances déjà installées, un
espace de données identifié avec sauvegarde/restauration décidées. Les données
sont sous ROOT_DIR/data et ses sous-espaces utilisateurs ; ce guide ne propose
pas une variable de relocalisation qui n'existe pas. Ne pas lancer deux checkouts
sur des données partagées ou recopier des données réelles dans des fixtures.

Depuis le dossier du checkout retenu, avec sa `.venv` disponible :

```powershell
$env:MOTIONEST_ENV = "development"
$env:MOTIONEST_PUBLIC_URL = "http://127.0.0.1:8766"
Remove-Item Env:MOTIONEST_LOCAL_AUTH_BYPASS -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m garmin_coach.dashboard --host 127.0.0.1 --port 8766 --no-browser
```

Si ce checkout n'a pas sa propre `.venv`, utiliser le chemin Python installé
déjà décrit dans `README_LOCAL.md`, tout en restant dans le bon checkout.
Pour le **test local explicite seulement**, remplacer le retrait du bypass par
`$env:MOTIONEST_LOCAL_AUTH_BYPASS = "true"` avant le démarrage, après contrôle
de cohérence signalé plus haut. Ouvrir `http://127.0.0.1:8766`, vérifier le bon
compte puis choisir le parcours sans Garmin. Aucun appel IA n'est requis pour
contrôler la page, les déclarations et l'historique vide.

`dashboard.main` lance le serveur, affiche son URL et ferme le socket dans son
finally. Arrêt existant : Ctrl+C dans le terminal propriétaire, attendre le message
d'arrêt. Reprise : même commande, même checkout et données préservées, puis login
si nécessaire. Ne pas recréer compte, supprimer données ou changer la clé de
session pour résoudre une simple reprise. Aucun mécanisme permanent de relance
n'est installé. Arrêter avant de changer de mode, puis retirer le bypass du
processus et relancer ; les comptes déjà vérifiés localement ne sont pas
automatiquement rendus non vérifiés par ce retrait.

## Erreurs, traces et limites d'exploitation

Le handler masque son log d'accès HTTP ordinaire (`log_message`), mais utilise
`LOGGER.exception` sur certains échecs. Des tracebacks internes peuvent donc
contenir des informations privées : aucune garantie de nettoyage exhaustif des
logs n'est établie. Conserver une trace locale restreinte, puis expurger avant
partage : mots de passe, clés IA/SMTP/Turnstile, tokens Garmin, cookies, liens de
reset/vérification, e-mails et titres/positions non nécessaires. Éviter dump
d'environnement, capture réseau complète ou impression des corps de requêtes.

L'outbox contient des liens actifs : ce n'est pas un journal public. L'intention
Garmin incertaine doit rester bloquée ; aucune édition JSON ou relance après
reconnexion pour la contourner. Les erreurs de transport Windows historiques
ne sont pas une cause démontrée : conserver le premier contexte d'échec avant
diagnostic, sans relance en boucle ou assouplissement d'assertions.

## P01–P12 : acquis et lacunes à fermer

Les tests validés du rapport pilote sont réutilisés ; aucun n'est relancé ici.

| Critères | Preuve disponible | Reste opérationnel |
|---|---|---|
| P01 | Livraison identifiée et validation antérieure | Root vérifie état/ascendance et clôture de son intégration ; ce document ne la réalise pas |
| P02, P07 | Sessions, auth et isolation synthétiques | Compte attendu, accès opérateur limité ; e-mail/anti-robot réels non démontrés |
| P03, P05, P06 | Parcours sans Garmin, contrats et erreurs testés | Observer uniquement nouveaux écarts du pilote ; pas de données synthétiques dans son historique |
| P04, P08 | Connexion tardive et livraison incertaine simulées | Aucun Garmin réel qualifié ; résolution volontairement absente |
| P09 | Sauvegardes de développement conservées | Responsable, emplacement et restauration opérationnelle des comptes du pilote à prouver |
| P10 | Serveur loopback existant | Machine, port et environnement effectivement retenus à vérifier avant accès participant |
| P11 | Campagne IA antérieure, réserve de suivi conservée | Fournisseur/droits/budget acceptés ; services et clés effectifs non vérifiés ici |
| P12 | Procédure de support/arrêt proposée | Nommer responsable et canal ; suspendre si accès croisé, perte de données ou effet distant imprévu |

Pour un futur accès distant : décision et autorisation séparées, HTTPS/proxy/URL,
mode production, cookies Secure, e-mails/Turnstile réels, bypass désactivé, politique
de sauvegarde et essai de restauration, restriction des traces et accès vérifiés.
Le token exigé par `dashboard.main` hors loopback ne transforme pas le serveur
local en hébergement validé. Ne pas élargir simplement `--host` au réseau.

Seul ce document est livré : aucune lecture de .env/.secrets réels, aucun code,
configuration, infrastructure, service, participant ou main modifié. Aucun test,
Garmin réel, IA payante, déploiement ou push exécuté.
