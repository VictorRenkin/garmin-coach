# Validation 3D hors ligne — séances complexes et doubles journées

Base : `ea52e6bfe6d5dfc7102913cc3652444079cec6a8` (`main` réel au départ).
Branche isolée : `codex/motionest-3d-offline-validation`.
Référence réutilisée : matrice `35df39f`,
`docs/MATRICE_3D_APRES_INTEGRATION_3C.md`. Aucun nouvel audit Garmin.

## Périmètre et couverture réutilisée

Seuls `tests/test_complex_double_day_3d.py` et le présent document sont ajoutés.
Aucun code applicatif, import, synchronisation, générateur ou stockage nouveau.
Les tests réutilisent `VerifiedClient`, les fixtures de blocs 3B et de calendrier
3C, les validateurs et les stores produits par Motionest. Le fournisseur de
génération est entièrement simulé ; aucun appel IA ou Garmin réel n'est effectué.

Les contrats élémentaires ne sont pas recopiés :

- secondes, répétitions, cibles et plafonds : `test_session_blocks_3b.py` et
  `test_complex_sessions_quality.py` ;
- autorisation de double journée, repos UTC, minuit, accord unitaire, retrait
  global et compatibilité : `test_calendar_3c.py` et `test_calendar_3c_quality.py` ;
- idempotence après IDs persistés, calendrier incertain, horodatage des faits :
  `test_garmin_delivery.py`.

## Combinaisons ajoutées

La fixture commune sauvegarde une proposition réelle via le fournisseur simulé.
Les deux séances complexes du même jour ont des IDs distincts, commencent à
08 h et 16 h avec offset et durent 35 et 38 minutes. Leurs deux blocs conservent
les récupérations 45/30 secondes puis 90/30 secondes, les répétitions et les
cibles FC. Chaque relecture réinstancie la proposition depuis le JSON validé.
Un nouveau client simulé conserve seulement l'état du service fictif précédent.
Il s'agit d'une simulation de reprise des stores, pas d'un processus réellement tué.

| Cas complémentaire | Assertions principales | État initial sur `ea52e6b` |
|---|---|---|
| Deux séances complexes, accords successifs | Zéro upload/programme avant accord ; matin accepté seul ; IDs de séances/workouts/événements distincts ; secondes exactes ; mêmes noms SDK ; relance des deux sans nouvel envoi | Réussi |
| Révision du bloc matinal après deux livraisons, puis reprise | Soir inchangé et toujours accepté ; matin périmé ; vérification idempotente du soir ; aucun nouvel upload du matin dans l'ancien événement | **Échec reproductible** |
| Interruption après sauvegarde de l'ID d'upload | L'ID est relu ; la programmation reprend une fois sans second upload | Réussi |
| Accusé de programmation perdu après succès simulé | Erreur incertaine puis confirmation du même événement ; aucune seconde programmation | Réussi |
| Confirmation calendrier absente | État pending conservé à la reprise ; confirmation ultérieure reconnue sans renvoi | Réussi |
| Calendrier absent ou malformé après première livraison | Refus de la seconde avant upload ; IDs et store connus préservés (deux cas) | Réussis |
| Upload sans ID confirmé | `workout_id_missing` ; zéro programmation et aucune création déclarée dans le store local | Réussi, avec réserve ci-dessous |

## Checkpoint de reproduction rouge

Première exécution ciblée : **7 réussis, 1 échec, 0 ignoré**, 5,34 s,
quatre avertissements Pydantic préexistants du SDK. Preuve ignorée :
`reports/phase-3d-offline-initial.xml`.

Le test défaillant est
`test_revision_of_complex_morning_preserves_evening_record_after_store_reload`.
Les deux uploads/programmes simulés ont déjà réussi. Une révision des blocs du
matin change son empreinte, tandis que les blocs, l'accord et la clé de livraison
du soir restent identiques. La relecture du soir est néanmoins refusée avec
`calendar_collision` : l'ancien workout matinal est encore présent dans le
calendrier fictif, mais n'est plus reconnu par les clés de la proposition révisée.

L'assertion métier est conservée : vérifier le soir déjà programmé ne doit ni
le modifier ni créer/programmer de nouvelle séance. En parallèle, le matin
révisé ne doit pas être envoyé sans nouvel accord, ni contourner la collision
avec son ancien workout après réacceptation. Élargir globalement l'allowlist aux
anciennes empreintes risquerait de supprimer cette seconde protection.

Ce défaut est reproduit sur la base 3C, avant combinaison avec 4A. Ce n'est pas
une régression numérique 4A. Il est transmis à l'orchestrateur et à l'agent A
pour un correctif applicatif isolé. L'agent C ne modifie aucun fichier applicatif.
Ce checkpoint historique **n'est pas une livraison verte**. La validation après
correction ci-dessous conserve le test et toutes ses assertions.

## Validation après correctif isolé

Le correctif applicatif de l'agent A est
`5230807e17729e127e0d80ea9eeb4e6fbeab2aaa`. La combinaison contient les tests
de reproduction C (`0f1b369`, transférés par cherry-pick `45c0aab`).

L'agent C a réexécuté indépendamment ses huit cas depuis le worktree combiné
de l'agent A, sans modifier ses sources : **8 réussis, 0 échec, 0 ignoré**,
3,78 s, quatre avertissements Pydantic préexistants du SDK.

Commande : `python -m pytest tests/test_complex_double_day_3d.py -q --tb=short
--junitxml=reports/phase-3d-offline-after-isolated-fix.xml`.
Le XML est conservé hors suivi dans le worktree
`motionest-integrate-numeric-4a/garmin-coach-main/reports/`.

Après les contrôles d'identité, d'accord, de date et de sport, le correctif
reconnaît uniquement une livraison déjà persistée dont le workout exact est
confirmé à la même date dans un calendrier strictement validé. Cette lecture
idempotente n'effectue ni upload ni programmation. Les nouveaux effets restent
soumis aux collisions existantes ; aucune autorisation globale des anciennes
empreintes n'est ajoutée. Le matin révisé reste bloqué sans nouvel accord et,
après accord, tant que son ancien workout crée une collision.

La branche C conserve intentionnellement son checkpoint de reproduction et
reçoit uniquement cet addendum documentaire : elle n'intègre aucun code
applicatif. Le résultat vert concerne précisément le SHA combiné ci-dessus,
pas l'ancien arbre applicatif du checkpoint rouge. L'agent A rapporte également
44 tests ciblés réussis, incluant ces huit cas et quatre gardes complémentaires.
L'agent C n'a lancé ni suite complète, ni appel IA, ni opération Garmin réelle.
La réserve distincte ci-dessous reste ouverte.

## Réserve distincte : accusé d'upload perdu

La matrice précédente ne garantissait pas une création serveur « exactly once ».
Le nouveau test constate qu'un retour sans ID provoque une erreur et aucune
programmation. Il ne peut pas savoir si un service distant aurait déjà créé le
workout avant de perdre sa réponse.

Une caractérisation synthétique séparée a fait retourner `{}` après chaque
création fictive : deux tentatives explicites produisent deux erreurs
`workout_id_missing`, deux créations fictives, zéro programmation et un store
local toujours vide. Preuve ignorée : `reports/phase-3d-lost-upload-ack.json`.
Cette simulation précise une limite préexistante, pas un résultat observé sur
Garmin. Aucun retry automatique ou moteur de rapprochement n'est ajouté.
Un résultat distant inconnu nécessite une réconciliation avant tout renvoi ;
la validation actuelle ne justifie aucune promesse d'idempotence de création
lorsqu'aucun identifiant n'a été reçu et persisté.

## Limites maintenues

Les tests inspectent les contrats locaux, DTO SDK et services simulés. Les
horaires 08 h/16 h restent dans Motionest : le client courant reçoit une date
seule pour `schedule_workout`. Deux noms SDK identiques n'empêchent pas des IDs
distincts dans les simulations, mais leur rendu matériel n'est pas validé.

Accès au programme officiel, compatibilité de l'API privée, réception serveur,
synchronisation et comportement par modèle/firmware restent des niveaux séparés.
Aucune publication, programmation, modification ou suppression Garmin réelle.
La phase 3D entière ne peut pas être annoncée validée par cette campagne hors ligne.
