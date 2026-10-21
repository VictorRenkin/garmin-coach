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
