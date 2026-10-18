# Phase 2B — Clarification des capacités sportives

Base : `main` à `f1e32bfc919c6ea02a93892fc88d0419a11c924d`, propre au départ.
Branche isolée : `codex/motionest-capabilities-2b`. Aucun changement de main.

Clôture : **839 tests réussis, 0 échec, 0 ignoré** après stabilisation du seul
banc HTTP. Voir la section de clôture pour le diagnostic et les résultats finaux.

## Capacités réutilisées

La matrice `CAPACITES_SPORTIVES_PHASE_2_PREPARATION.md` reste la référence.
Seules ses évolutions connues ont été vérifiées : la fraîcheur multisport est
résolue par 2A ; `PlanSessionV2.sport` représente plusieurs sports, alors que
`garmin_delivery.build_plan_session_workout` et les contrôles de livraison n’acceptent que
`running`. Cette représentation ne constitue pas un générateur spécialisé.
`GoalV1` conserve les intentions progress/race/beginner/comeback et les événements
5 km, 10 km, semi, marathon, trail et distance personnalisée de course.
Les synthèses historiques, recherche ancienne, mémoire et imports sont réutilisés.

## Ambiguïtés confirmées et corrections

- L’onboarding disait « Intégrer mes autres sports » et transmettait « intégrer
  les autres sports » au coach : remplacé par la prise en compte de leur charge.
- Le choix « Progresser » et le formulaire d’objectif ne précisaient pas la
  course : le texte d’aide indique désormais le périmètre course à pied.
- Le dashboard et la présentation de l’envoi legacy indiquent désormais la course.
  Le message existant de refus d’export non running était déjà correct et reste intact.
- Les consignes du coach indiquaient seulement que l’historique ne prouvait pas
  la présence d’un générateur : elles distinguent maintenant sport observé,
  analysable, objectif reconnu, séance générable et publication possible.
- Un premier essai IA vélo a promis de pouvoir enregistrer l’objectif cycliste,
  sans action effective. La consigne précise désormais intention conversationnelle
  versus objectif structuré de course. Le nouvel essai refuse explicitement cet
  enregistrement, sans convertir les 150 km cyclistes en objectif de course.

Les valeurs HTML et les contrats sont conservés. Aucun générateur, contrat,
import, synchronisation ou mécanisme de livraison n’a été modifié.

## Fichiers

- `garmin_coach/coach_chat.py` : consignes factuelles de capacité et priorité sur l’onboarding.
- `garmin_coach/dashboard_v2.html` : formulations uniquement, sans refonte.
- `tests/test_sports_capabilities_2b.py` : 10 cas de transport des consignes, 5 profils
  et 2 chemins de fournisseur. Ces tests ne prouvent pas à eux seuls le respect
  des consignes par un modèle réel.
- `scripts/validate_capabilities_2b.py` : réutilise le banc synthétique 2A avec
  5 profils, 2 échanges chacun ; nécessite explicitement `--real`.
- Le présent rapport.

## Validation initiale (avant stabilisation)

Tests ciblés finaux : **143 réussis, 0 échec, 0 ignoré**.
Première suite complète : **838 réussis, 1 échec, 0 ignoré** en 163,73 s.
Échec de `test_logout_endpoints_expire_only_the_expected_session_cookie` :
connexion HTTP locale interrompue par Windows (`WinError 10053`), déjà observée
avant 2B. Aucun fichier de cette fonctionnalité modifié. Relance du module
`test_multitenant_security.py` : **17 réussis, 0 échec, 0 ignoré**.
Suite complète finale : **838 réussis, 1 échec, 0 ignoré** en 159,51 s.
Même test de déconnexion et même WinError 10053. La suite complète n’est donc
pas entièrement verte ; aucune validation globale sans réserve n’est déclarée.
L’origine exacte de l’interruption n’a pas été démontrée durant cette mission.
Le module isolé passe et aucun changement 2B ne touche son mécanisme.
Le dernier XML est `reports/phase-2b-full-final.xml`.
Quatre avertissements Pydantic préexistants dans la dépendance garminconnect.

IA : **12 appels réels réussis techniquement**, sur profils synthétiques uniquement :
10 appels coureur, triathlète, football, multisport et objectif vélo non pris en charge ;
puis 2 appels vélo après précision de la consigne. Total **95 869 tokens**
(92 810 en entrée, 3 059 en sortie), selon le suivi du fournisseur existant.
Aucun compte ni appel Garmin réel ; le banc utilise FakeGarmin et des répertoires
utilisateurs temporaires. Le filtre existant vérifie l’absence des marqueurs privés
synthétiques dans les payloads envoyés. Aucune donnée utilisateur réelle injectée.

Les 12 réponses sont action=none dans ces demandes de clarification de capacités.
Le triathlète insistant n’obtient aucun faux plan ; la discussion récupération
football reste possible. Le coureur comprend que décrire une capacité n’est pas
créer ou publier un plan. Les réponses multisports distinguent analyse et génération.
Preuves locales ignorées par Git : `reports/capabilities-2b-real.json`,
`reports/capabilities-2b-unsupported-final.json`, XML ciblés et complets.

## Limites et arrêt

Les consignes conversationnelles réduisent les mauvaises affirmations sans
constituer une garantie déterministe sur toute formulation future. Les contrôles
d’exécution Garmin existants restent la protection opérationnelle. Aucun nouveau
filtre de classification sportive n’est créé. Pas de test visuel navigateur :
modification textuelle, aucune disposition ou interaction modifiée.
La dernière réponse vélo contient des caractères littéraux `\\n\\n` dans un message :
formatage conservé pour traitement ultérieur, hors clarification des capacités.
Les anomalies de calcul, addition de distances, contrôle de texte et complétude
signalées en 2A restent hors périmètre. Aucun développement de phase suivante.

Aucun push, aucune publication Garmin, aucune suppression de sauvegarde.
La variante `04aece0` et les anciens worktrees restent accessibles.

## État Git initial (avant clôture)

Modifications locales non commitées dans le seul worktree 2B : 2 fichiers suivis
modifiés et 3 nouveaux fichiers (test, script et rapport). Aucun commit créé.
`main` reste propre à `f1e32bfc919c6ea02a93892fc88d0419a11c924d`.
Le checkout original avec ses modifications préexistantes et `sites/` n’a pas été
modifié. Tous les anciens worktrees et la variante `04aece083b9e307fa38aba94feab7ea417e00877`
restent présents. La phase 2B attend validation avant toute intégration.

## Clôture et stabilisation du 18 octobre 2026

### Diagnostic reproduit sur 2B et sur main

Le serveur est lié à une adresse loopback et un port éphémère dès sa construction.
Son thread démarre avant les requêtes. L’arrêt appelle shutdown, join puis
server_close ; dans les 36 essais contrôlés, tous les threads se sont arrêtés.
Aucune preuve d’un défaut métier de session ou d’une course au démarrage du
serveur n’a été trouvée.

Le helper `_post` envoyait un corps JSON `{}` aux deux routes de déconnexion.
Or leurs handlers répondent et ferment la connexion sans consommer ce corps.
`urllib` peut envoyer les en-têtes et le corps en deux écritures : si le serveur
ferme entre les deux, Windows interrompt la connexion. Un client socket contrôlé,
utilisant le vrai DashboardHandler inchangé, reproduit précisément WinError 10053
en séparant en-têtes et corps par 50 ms. Ce délai appartient uniquement au
diagnostic ; aucune attente ni relance automatique n’est ajoutée au test.

Trois essais par route et par condition, sur les deux routes :

| Requête | Branche 2B avant correction | main sans 2B |
|---|---|---|
| En-têtes et JSON dans une même écriture | 6 réussis / 0 échec | 6 réussis / 0 échec |
| En-têtes puis JSON après 50 ms | 0 réussi / 6 échecs 10053 | 0 réussi / 6 échecs 10053 |
| POST sans corps | 6 réussis / 0 échec | 6 réussis / 0 échec |

Le test original non modifié passe aussi seul sur main (1 réussi), ce qui reste
compatible avec une course de transport. La reproduction contrôlée démontre que
le problème préexiste aux corrections de capacités sportives.

### Stabilisation minimale

Les vrais boutons dans `dashboard_v2.html` et `manager.html` utilisent un POST
sans corps. Le helper du test accepte désormais payload=None et ne sérialise
que les vrais payloads JSON. Les deux appels logout omettent le corps pour
reproduire la requête réelle. Les autres appels JSON restent identiques.
Les assertions de statut, redirection et cookie attendu sont intégralement
conservées. Une comparaison AST confirme que les 100 assertions du module
sont identiques à main. Aucun retry, skip, timeout supplémentaire ou assertion assouplie.
Aucun changement d’authentification ni de code serveur.

La fermeture avec corps inattendu non consommé reste une limite de transport
des routes actuelles pour des clients qui envoient du JSON à logout. Le correctif
ne prétend pas résoudre ce comportement pour de tels clients ; il stabilise le
scénario testé en l’alignant sur les interfaces existantes.

### Résultats de clôture

- Test logout après correction : 1 réussi, 0 échec, 0 ignoré.
- Module de sécurité : 17 réussis, 0 échec, 0 ignoré.
- Tests ciblés de phase 2B : 143 réussis, 0 échec, 0 ignoré.
- Suite complète : **839 réussis, 0 échec, 0 ignoré** en 153,74 s.
- Aucun nouvel appel IA ou Garmin réel durant cette clôture.
- Les quatre avertissements Pydantic de garminconnect restent préexistants.

XML locaux : `reports/phase-2b-stabilisation-{logout,security,targeted,full}.xml`.
Diagnostic reproductible conservé dans `reports/diagnose_logout_transport.py`,
résultats `reports/logout-transport-{2b-before,main}.json`.

### Préservation et livraison

Les cinq fichiers initiaux ont été archivés avant correction avec vérification
SHA-256 de chaque entrée. Les quatre fichiers de code/test/script 2B sont restés
strictement identiques à leur empreinte initiale ; seul le rapport est complété.
Sauvegarde et manifeste : `reports/backups/phase-2b-stabilisation-20261018/`.
Le même dossier conserve une archive du diagnostic et de ses résultats.

Le diff final comprend ces cinq fichiers et la seule stabilisation supplémentaire
dans `tests/test_multitenant_security.py`. Aucun autre fichier applicatif modifié.
Le commit local de clôture regroupe les six fichiers sur
`codex/motionest-capabilities-2b` uniquement, directement au-dessus de la base main.
Les contrôles main propre et ascendance sont vérifiés après commit ; leur résultat
et le SHA exact sont consignés dans `reports/phase-2b-stabilisation-delivery.json`.
L’intégration proposée est uniquement `git merge --ff-only codex/motionest-capabilities-2b`
depuis le worktree main, après validation humaine et recontrôle de sa propreté.
Cette commande n’a pas été exécutée. Aucun push.

Les onze worktrees restent accessibles. Le checkout original détaché à `68c704a`
conserve ses 16 entrées locales, dont `sites/` ; le worktree design conserve sa
modification préexistante. Les huit autres worktrees hors 2B sont propres, dont
main et la variante préservée à `04aece0`. Aucun ancien worktree supprimé.
