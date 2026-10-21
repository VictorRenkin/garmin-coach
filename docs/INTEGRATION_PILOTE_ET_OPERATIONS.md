# Intégration pilote Web et préparation opérationnelle

## Résultat

Mission du 21 octobre 2026. La livraison pilote validée est intégrée localement
par fast-forward uniquement. Aucun nouveau code applicatif développé.

| Référence | SHA |
|---|---|
| Main avant | 839fd052d610445bad4c71e771a26524deb1cafb |
| Livraison finale vérifiée | f44011484977019394f2462d0af4ef160501438f |
| Main après | f44011484977019394f2462d0af4ef160501438f |
| Code/tests/scripts de la validation complète | afadd9ba10d95b25d5b87f902eb4050c12374b55 |
| Arbre main et livraison | 2a168dde0e0b2445840210a0718bbbd2b2ac32be |

Commande effectuée dans le checkout main :

```text
git merge --ff-only codex/motionest-pilot-preparation
```

Avant intégration : main propre et ancêtre de la candidate, worktree candidate
propre, huit fichiers exacts contrôlés. Diff code/tests/scripts entre afadd9ba
et f440114 vide ; seuls les rapports ont évolué après la suite complète. Le
catch d'actualisation affiche un message puis restaure le bouton : pas de retry
automatique, nouvel effet Garmin ou changement de moteur d'import. Les preuves
de la mission précédente sont réutilisées, sans reprendre ses développements.

Les nouveaux guides opérationnels et le test de restauration restent sur
**codex/motionest-pilot-operations**, séparés de main. Le commit final de cette
branche contient ce rapport. Aucune intégration automatique de ces ajouts.

## Tests et contrôles

| Campagne | Réussis | Échecs / erreurs | Ignorés | Durée |
|---|---:|---:|---:|---:|
| Suite complète antérieure sur code identique, XML vérifié | 1 113 | 0 / 0 | 0 | 243,83 s |
| Ciblés exécutés sur main après intégration | 159 | 0 / 0 | 0 | 39,69 s |
| A, nouvelle restauration isolée synthétique | 1 | 0 / 0 | 0 | 3,03 s |
| Root, restauration et gardes d'incertitude combinées | 23 | 0 / 0 | 0 | 5,66 s |

Ces campagnes se recouvrent, ne pas les additionner. La suite complète n'est pas
relancée : sources applicatives, tests et scripts de la livraison sont ceux
validés, même Python/venv existant, aucune installation ou reconfiguration de
l'environnement. Les nouveaux ajouts opérationnels n'altèrent pas le produit ;
leur seul test nouveau est exécuté séparément avec 22 gardes existants.
Ne pas annoncer 1 114 tests complets exécutés : ce total n'a pas été lancé.

Les 159 ciblés couvrent les deux nouveaux modules pilote, parcours HTTP phase 6,
interface, sécurité multicomptes, onboarding, découverte, livraison Garmin
simulée, accords et calendrier. Les 23 combinés sont
`test_pilot_backup_restore.py` et `test_garmin_upload_uncertain.py`.
Quatre avertissements SDK/Pydantic préexistants ; aucun incident HTTP observé
dans ces contrôles. La cause de l'ancien WinError 10053 reste indéterminée.

Le premier essai A avait une erreur du nouveau test (fonction de chargement de
comptes inexistante) ; le test seul a été corrigé. Son rapport conserve cet essai,
sans modification du code applicatif ni masquage d'un échec final.

Preuves locales ignorées : dans le checkout main,
`reports/pilot-integration-targeted.xml/log` ; dans le worktree opérationnel,
`reports/pilot-operations-preflight.json`, `pilot-operations-restore.xml/log` et
`pilot-operations-final.json`. Le XML complet antérieur reste `pilot-full.xml`.

## Sauvegarde et restauration

Guide A : [PILOTE_SAUVEGARDE_RESTAURATION.md](PILOTE_SAUVEGARDE_RESTAURATION.md).

Préserver ensemble `data/` (comptes globaux, espaces par utilisateur et legacy,
profil, historique, messages/mémoire, propositions, accords et livraison) et
`.secrets/` (clé de signature des sessions et tokens). Conserver version et
configuration nécessaires séparément, sans afficher les secrets. Les archives
contenant ces éléments doivent rester privées et à accès restreint.

La procédure proposée arrête tous les writers, copie vers une destination neuve,
compare les manifestes SHA-256 puis reprend le service. Les écritures atomiques
individuelles existantes ne constituent pas un snapshot global. La restauration
se fait dans un espace neuf sans réseau, jamais par superposition sur un compte
actif ; contrôle des fichiers et des liens entre accords, séances et livraisons
avant toute reprise.

Preuve nouvelle : deux comptes et stores synthétiques, copies dans un
TemporaryDirectory, empreintes identiques, cookie relisible, accord et ID
confirmé repris idempotemment, intention sans ID toujours bloquée, Bob sans
livraison ni token d'Alice, source inchangée. Revue indépendante B : aucun
blocage. Ce n'est pas une restauration de compte réel, un redémarrage intégral
du serveur, un test d'ACL/chiffrement, une résistance à coupure ou une reprise
réseau. Le responsable, le stockage effectif et les protections opérationnelles
de la sauvegarde du futur pilote restent à fixer.

Une ancienne copie peut perdre les intentions/IDs créés après sa date ; restaurer
localement n'annule aucun effet Garmin. Ne jamais interpréter cette restauration
comme autorisation de renvoi ni retirer une intention pour débloquer un compte.

## Configuration locale accompagnée

Guide B : [PILOTE_CONFIGURATION_LOCALE.md](PILOTE_CONFIGURATION_LOCALE.md).

Utiliser un checkout retenu, une seule instance, loopback `127.0.0.1`, port choisi
et URL publique locale cohérente, lancement existant `--no-browser`, arrêt Ctrl+C.
Choisir explicitement mode normal en développement avec confirmation via outbox,
ou mode de test local accompagné décrit dans README_LOCAL. Le bypass local ne
prouve pas une vérification d'identité réelle ; le retirer ne rend pas les
comptes déjà vérifiés localement automatiquement non vérifiés.

Point de configuration vérifié par code : `load_settings` recharge `.env` avec
`override=True`. Des variables PowerShell seules ne garantissent donc pas le
mode effectif. L'opérateur doit garantir la cohérence, sans imprimer ses secrets.
Aucun `.env` réel n'a été ouvert ou modifié pour cette mission.

Les cookies existants ont HttpOnly/SameSite=Strict, Secure en production ; la clé
de session doit rester protégée. Sans SMTP, les liens de confirmation restent
locaux et confidentiels. Le mode de test ne qualifie ni e-mail SMTP ni Turnstile
réels. Aucun fournisseur IA contacté : configuration, autorisation et budget sont
à décider si la conversation réelle entre dans le pilote.

La sortie d'accès HTTP ordinaire est masquée, mais certains LOGGER.exception
produisent des tracebacks internes : aucun nettoyage exhaustif n'est garanti.
Journaux/outbox doivent rester restreints et expurgés avant partage ; pas de dump
d'environnement, cookie ou lien actif dans un ticket.

Pour un futur accès distant : décision séparée, preuve sur HTTPS/proxy/URL,
mode production, cookies, SMTP/anti-robot réels, bypass absent, stockage et
restauration, droits d'accès et traces. Élargir le host ou définir le token
d'écoute réseau ne vaut pas validation de déploiement. Aucun serveur public
déployé, service configuré ni infrastructure installée.

## Parcours, retours et support

Guide C : [PILOTE_PARCOURS_ET_SUPPORT.md](PILOTE_PARCOURS_ET_SUPPORT.md).

Dix tâches avec succès observables : accès personnel, objectif, disponibilités,
choix sans Garmin, compréhension de l'historique vide, édition de profil,
navigation, coach si autorisé/configuré, relecture après recharge et déconnexion.
Une tâche non disponible reste non essayée. Aucun plan promis à tout nouveau
compte découverte et aucune activité fictive ajoutée pour remplir un écran vide.

La grille note session pseudonyme, tâche, résultat seul/aidé/bloqué/non essayé,
attendu/observé et gravité. Pas de copie de conversation, secret ou historique
privé. Le support reproduit les défauts avec des données synthétiques plutôt que
de demander un export brut au sportif.

Arrêter la session concernée en cas d'accès croisé, secret affiché, disparition
persistée ou effet Garmin inattendu ; conserver le premier contexte expurgé,
ne pas multiplier les relances ni écraser les informations manquantes. Définir
responsable, canal privé, conservation et suivi avant invitation. Aucun utilisateur
contacté, compte participant créé ou retour réel collecté.

Le parcours proposé exclut Garmin réel par consigne d'accompagnement, sans nouveau
verrou technique : le connecteur existe toujours. La compatibilité réelle, les
horaires et la montre ne sont pas prouvés. Le blocage d'upload incertain persiste,
sans déblocage manuel, suppression, rapprochement distant ou édition JSON.
Le coach conserve la réserve qualitative de suivi IA déjà documentée ; son
utilisation réelle et sa revue humaine ne sont pas automatiquement autorisées.

## État objectif P01–P12 après cette mission

| Critère | Acquis réel | Encore nécessaire avant le périmètre participant retenu |
|---|---|---|
| P01 | Intégration locale FF, arbres identiques, ciblés verts ; code de la suite complète inchangé | Aucun développement à rejouer ; décision séparée pour intégrer les guides opérationnels |
| P02 | Parcours/sessions et refus testés en HTTP synthétique ; modes locaux décrits | Choisir et vérifier le mode effectif ; services e-mail/anti-robot réels si retenus |
| P03 | Sans Garmin : profil/découverte/états vides déjà démontrés | Expliquer le périmètre au sportif ; IA seulement si décidée et configurée |
| P04 | Connexion tardive et quatre récupérations simulées | Garmin réel non qualifié et exclu du parcours proposé |
| P05 | Contrats, séances et consentements déjà validés | Présentation exacte des capacités, pas de promesse de générateur supplémentaire |
| P06 | Erreur d'actualisation corrigée et reprise manuelle testée | Observer les nouveaux écarts ; incident Windows historique non expliqué |
| P07 | Isolation applicative réutilisée, restauration synthétique sans partage de token/store | Restreindre effectivement accès disque, journaux/outbox et canal de support |
| P08 | Intention incertaine bloquante et ID confirmé idempotent, même après restauration synthétique | Aucun résolveur livré ni autorisation de renvoi par restauration |
| P09 | Procédure froide proposée et restauration isolée vérifiée sur synthétique | Désigner responsable, destination protégée, fréquence et contrôle effectif sur l'environnement retenu |
| P10 | Guide local et serveur loopback existant ; aucun changement de production | Choisir poste/port/mode et vérifier la configuration effective avant accès participant |
| P11 | Validation IA antérieure 4 satisfaisants + 1 partiel conservée | Fournisseur, droits/budget et revue humaine si chat réel retenu ; pas de nouvel appel ici |
| P12 | Parcours sportif, grille, arrêt et signalement préparés | Nommer responsable/canal, durée et suivi ; aucune invitation par cette mission |

Le verdict reste **prêt sous conditions pour un pilote local accompagné**.
P09/P10/P12 progressent par une preuve synthétique et des guides utilisables,
mais leur mise en place effective n'est pas déclarée réalisée. Le pilote n'est
pas lancé. L'hébergement distant et Garmin réel exigent leurs propres décisions
et preuves ; les simulations ne les valident pas.

## Branches et conservation

| Rôle | Branche | Commit livré |
|---|---|---|
| Candidate intégrée | codex/motionest-pilot-preparation | f44011484977019394f2462d0af4ef160501438f |
| A sauvegarde/restauration | codex/motionest-pilot-backup-restore | 816472a59743b290913bbc8b1f391403b4f3209a |
| B configuration | codex/motionest-pilot-local-config | 470e2d51406b688f6b09d2a9c774a63a5b756218 |
| C support | codex/motionest-pilot-support | 53c9b63b729c6776c658ef154286ace257c672e3 |
| Consolidation séparée | codex/motionest-pilot-operations | Commit final contenant ce rapport |

La consolidation opérationnelle ajoute seulement quatre documents, dont ce
rapport, et un test synthétique. Diff applicatif/scripts avec main vide. Les
fusions conservent l'ascendance sans cherry-pick en double. Main et worktrees
livrés propres, contrôles `git diff --check` conformes.

Conservation : 42 worktrees, anciennes branches inchangées hors mouvements
autorisés, quatre archives CRC valides, 24 empreintes originales hors natif
identiques, variante historique 04aece083b9e307fa38aba94feab7ea417e00877 accessible.
Les deux worktrees auparavant modifiés gardent leurs états : original détaché
68c704a, 16 entrées Git ; design 1a2d28b, une entrée. Aucun changement sites ou
lecture de contenu natif. Aucun reset destructif, suppression, push, participant,
compte/upload Garmin réel, déploiement, IA payante ou relance permanente.

En cas d'interruption, les SHA, résultats et décisions restantes sont dans ce
rapport et `reports/pilot-operations-resume.json`. Aucun mécanisme de reprise
automatique garanti. Arrêt après les missions autorisées, sans lancement du pilote.
