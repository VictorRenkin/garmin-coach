# Pilote — sauvegarde cohérente et restauration isolée

Base de préparation : `f44011484977019394f2462d0af4ef160501438f`. Procédure proposée, pas une sauvegarde de compte réel exécutée. Aucun fichier de données ou secret réel n'a été lu.

## Périmètre établi par le code

| Élément à préserver ensemble | Preuve dans le code |
|---|---|
| `data/users.json` : comptes, hashes/sels, vérification et informations de récupération | `user_accounts.py`, `USERS_FILE`, `_load_users`, `_save_users` |
| Tout `data/users/<id>/`, sans renommer les identifiants ; `data/legacy/` si utilisé en CLI | `config.py`, `_user_root`, `_UserPath`, `user_context` |
| Profil, entrées V1, onboarding, recommandations/décisions, proposition V2 et accords V2 | `config.py` lignes 85–97 ; `plan_acceptance.py`, `_current_state` et `record_plan_acceptance_decision` |
| Historique/import, détails multisport, activités, analyses, plans et rapports | `config.py` lignes 76–96 : répertoires dynamiques et `history_import.json` |
| Messages, brouillons et mémoire durable | `coach_chat.py`, `MESSAGES_FILE`, `COACH_DRAFTS_DIR` ; `config.COACH_MEMORY_FILE` |
| `garmin_delivery_v1.json`, intégralement, avec identifiants, empreintes, `upload_requested_at`, `schedule_requested_at` | `garmin_delivery.py`, `_read_store`, `_write_store`, `_session_delivery_key`, `_uncertain_upload` |
| `.secrets/web_session.key` et `.secrets/garmin_tokens/<id>/` | `user_accounts.SESSION_KEY_FILE`, `_session_key`, `create_session`, `user_from_cookie` ; `config._UserPath(token=True)` |

Sauvegarder intégralement `data/` et `.secrets/`, plutôt qu'une sélection des seuls fichiers visibles, évite de désolidariser les consentements, propositions et faits de livraison. Conserver aussi séparément le SHA/version applicative, les dépendances verrouillées disponibles et la configuration d'exploitation nécessaire, dont les éventuels fichiers `.env`/configuration fournisseur. Ces éléments peuvent contenir des secrets : ne pas les imprimer dans les journaux, tickets ou dépôt Git. Leur validité externe n'est pas prouvée par une copie.

La clé de session permet de relire les cookies existants ; sa rotation volontaire les invalide. Ne jamais importer une clé ou des tokens de production dans un environnement pilote partagé. Les archives contenant comptes, conversations ou credentials doivent rester privées, à accès restreint et protégées au repos ; aucune solution de chiffrement nouvelle n'est introduite ici.

## Sauvegarde proposée

1. Fixer un créneau et arrêter proprement serveur Web, CLI, boucle de synchronisation et workers du même checkout. Vérifier qu'aucun autre processus ne peut écrire ces répertoires. Une fermeture du navigateur seule ne suffit pas.
2. Copier `data/` et `.secrets/` dans une destination neuve hors des répertoires sources, avec les paramètres nécessaires conservés séparément. Ne pas écraser une sauvegarde précédente. Noter le SHA, l'heure et le périmètre ; ne pas journaliser le contenu.
3. Produire un manifeste de chemins relatifs et empreintes SHA-256, comparer source figée et copie, puis conserver le manifeste avec l'archive. Vérifier également les fichiers absents/présents et les erreurs de lecture, pas uniquement le nombre de fichiers.
4. Reprendre le service seulement une fois la copie vérifiée. Conserver au moins un point antérieur indépendant et contrôler périodiquement une restauration isolée.

Les écritures atomiques existantes (`garmin_delivery._write_store`, `user_accounts._save_users`) protègent le remplacement individuel, pas une transaction entre tous les fichiers. Aucun mécanisme de snapshot global ou restauration opérationnelle dédié n'est revendiqué. Une copie à chaud est insuffisante pour cette procédure.

## Restauration proposée, isolée et réversible

1. Préparer un répertoire/checkout neuf correspondant au SHA sauvegardé ; ne jamais superposer des données anciennes sur un compte actif. Garder source et archive inchangées.
2. Interdire toute sortie réseau et ne pas démarrer automatiquement serveur, scheduler, import, fournisseur IA ou client Garmin. Copier l'ensemble dans ce répertoire vide et comparer le manifeste avant tout démarrage.
3. Vérifier hors ligne les chemins par compte, la lisibilité des JSON et les liens proposition/empreinte/accord/livraison. Un store illisible doit rester une erreur, jamais être remplacé par un store vide pour débloquer l'interface.
4. Vérifier avec clients synthétiques qu'un ID confirmé reste confirmé, qu'une intention sans ID bloque une nouvelle création et qu'un autre compte n'hérite ni des accords ni des tokens. Ne pas supprimer l'intention incertaine pour pouvoir réessayer.
5. Prévoir une validation humaine avant toute réactivation réelle. Une restauration d'un point **antérieur à un effet distant** peut perdre l'ID ou l'intention déjà enregistrés après ce point : aucune sauvegarde locale ne permet d'annuler ou détecter universellement cet effet. Conserver les traces récentes et résoudre cette divergence avant toute publication ; ne pas prétendre qu'un rollback local annule Garmin.

## Preuve synthétique exécutée

Lacune de preuve locale : aucun test dédié de restauration n'était identifié. `tests/test_pilot_backup_restore.py` réutilise les fixtures existantes 3D et les stockages réels ; aucun script de production ou code applicatif n'est ajouté.

Le test crée deux identités synthétiques (Alice sous ID opaque de la fixture et Bob), profil/entrées/mémoire/messages, une activité locale, une clé de session et un token fictif. Un `VerifiedClient` existant confirme une séance ; une seconde création simulée perd son ID et persiste l'incertitude. Deux copies sont faites dans un `TemporaryDirectory` indépendant, puis les chemins de lecture sont redirigés vers la restauration. Tous les fichiers ont les mêmes empreintes avant contrôle ; cookie toujours vérifiable, deux comptes présents, ID confirmé/idempotence conservés, accord accepté encore utilisable, retry incertain refusé, Bob sans livraison/token. La source temporaire reste identique.

Commande ciblée : `python -m pytest tests/test_pilot_backup_restore.py -q --junitxml=reports/pilot-backup-restore-final.xml`.

**1 réussi, 0 échec, 0 ignoré, 3,03 s**, quatre avertissements SDK Pydantic préexistants. Un premier essai a échoué sur une erreur du nouveau test (nom de fonction de chargement de comptes inexistant) ; seule cette fixture/assertion a été corrigée, sans changement applicatif. XML initial et final locaux conservés.

Portée : copie de fichiers figés et comportement local restauré avec faux Garmin, sans redémarrage complet du serveur ni authentification réelle. Non prouvés : sauvegarde à chaud, résistance à coupure disque, ACL/chiffrement effectifs de l'opérateur, tokens réels, e-mail, réseau, montre et reprise de tâches en vol. Aucun compte réel restauré, aucun secret réel lu, aucun accès à l'application native hors périmètre, aucun appel Garmin/IA ni push.
