# Checkpoint : upload Garmin sans accusé confirmé

Base `ee41c9e822c79060afa837f7f66c6db08e69dbf9`, branche isolée
`codex/motionest-garmin-upload-intent`. La pièce jointe reçue se termine
littéralement à « 6. Vérifier si une intention ». Aucune suite n'est présumée.
Aucun code applicatif modifié à ce checkpoint.

## Preuve réutilisée et reproduction complémentaire

Références : `STRATEGIE_REPRISE_GARMIN_INCERTAINE.md`, matrice 3D `35df39f`,
`VALIDATION_3D_HORS_LIGNE.md`, tests d'idempotence et de reprise existants.
La stratégie antérieure n'est pas une implémentation. La recherche ciblée et la
lecture de `garmin_delivery.publish_accepted_session` confirment l'absence
d'intention d'upload sauvegardée avant appel. `_write_store` est atomique, mais
n'est utilisé dans cette branche qu'après réception d'un ID exploitable.
Les données de programmation sont ensuite sauvegardées avant leur appel distant.

Deux cas dans `tests/test_garmin_upload_uncertain.py` réutilisent `pair`,
`CapturingClient`/`VerifiedClient`, les accords et les vrais stores temporaires :
création fictive puis réponse sans ID, et création fictive puis `TimeoutError`.
Pour chacun, deux appels explicites produisent deux créations fictives, zéro
programmation, deux erreurs et un store de livraison vide. Résultat : **2 réussis,
0 échec, 0 ignoré**, 5,87 s ; quatre warnings SDK préexistants.
Ces assertions caractérisent le défaut actuel, pas le contrat futur souhaité.
Aucun service Garmin réel n'a été appelé ni défaut serveur affirmé.

## Proposition bornée à coordonner

Réutiliser le store existant : écrire durablement une intention horodatée avant
l'upload ; sans ID sauvegardé, refuser les relances avec un état d'incertitude.
Si l'écriture préalable échoue, ne pas appeler le client. Si le retour ou la
sauvegarde d'ID échoue, l'intention subsiste et empêche la recréation aveugle.
Après ID reçu et sauvegardé, conserver le chemin existant et ses garde-fous.

Ne pas contourner l'incertitude en révisant le même `session_id`. Le traitement
legacy sans identité stable doit être arrêté avec l'orchestrateur ; aucune
association automatique par nom ou proximité de contenu n'est justifiée.
Préserver ordre des gardes : utilisateur, proposition actuelle, accord unitaire,
identité, date, sport et collisions. Ne pas annoncer présence sur montre.

Aucune recherche distante, suppression, rapprochement, retry réseau nouveau ou
réinitialisation d'intention proposé dans cette étape. Les retrys des couches
transport ne sont pas rendus exactly-once par une intention locale. Les anciennes
tentatives sans trace ne deviennent pas réconciliables rétroactivement.

Code applicatif en attente de coordination root compte tenu de la consigne
tronquée ; aucun main, push, mobile-app, IA payante ou suite complète touché.

## Mise en œuvre conservatrice autorisée après coordination

L'orchestrateur a confirmé que l'objectif général explicite de renforcer la
livraison en cas de réponse perdue, et la stratégie citée, autorisent ce garde
minimal. Aucune suite du texte tronqué n'a été inventée. Le checkpoint initial
`ae692f3` conserve la reproduction avant correction.

Le même store et schéma 1 reçoivent `upload_requested_at` avant le POST.
Une écriture préalable échouée ne déclenche aucun upload. Sans ID sauvegardé,
les relances sont refusées avec `upload_uncertain`, avant tout accès calendrier,
upload ou programmation. Un timeout est converti en erreur statique sans détails
fournisseur exposés. Un ID non confirmé ou invalide (booléen, flottant, nul,
négatif, chaîne non numérique ASCII) laisse l'intention en place.

Après ID valide sauvegardé, les reprises et horodatages existants restent
inchangés. Aucune recherche, suppression, résolution, effacement d'intention ou
relance automatique n'est implémentée. `assert_upload_retry_safe` permet à la
route web d'effectuer le contrôle local avant connexion du client ; publish
le répète sous le verrou existant après ses gardes métier. Le helper ne remplace
pas les contrôles de session, consentement ou identité de la route.

La projection publique expose `garmin_connect_status=unknown`,
`upload_status=uncertain` et un message statique, même après révision du même
`session_id`. Après ID connu : `created`/`confirmed`. Sans tentative :
`not_created`/`not_requested`. B gère l'affichage sans bouton de renvoi ; A
intègre le précontrôle HTTP séparément, sans moteur supplémentaire.

Une intention legacy sans identité bloque conservativement les nouvelles
créations du compte concerné, car l'attribution est improuvable ; une livraison
actuelle dont l'ID est déjà confirmé peut toujours être relue et reprise.
Les autres comptes utilisent leurs stores isolés. Une révision d'une séance
identifiée ne contourne pas l'intention attachée à son identité stable.

Les deux attentes existantes 3D ont été ajustées avec autorisation root :
interruption ciblée sur l'écriture contenant l'ID (et non la nouvelle intention),
puis attente d'intention sauvegardée après réponse sans ID. Les assertions
d'identifiants, de nombre d'uploads et de programmation sont conservées.

Validation ciblée : 76 réussis, zéro échec/ignoré, 14,42 s (livraison, nouveaux
tests, doubles complexes, acceptation et qualité calendrier), quatre warnings
Pydantic préexistants. Deux assertions finales renforcent ensuite projection
après révision et reprise d'ID confirmé malgré intention legacy.
Module final après renforcement : 19 réussis, zéro échec/ignoré, 6,63 s.

Limites explicites : anciennes tentatives sans record indétectables ; retries
internes du SDK/transport non prouvés ; verrou RLock intra-processus uniquement ;
atomicité de remplacement fichier sans garantie fsync/coupure d'alimentation.
La persistance d'intention ne garantit donc pas exactly-once serveur ou sécurité
multi-processus. Un arrêt après intention mais avant POST peut bloquer sans
création distante : choix conservateur volontaire, sans déblocage automatique.
