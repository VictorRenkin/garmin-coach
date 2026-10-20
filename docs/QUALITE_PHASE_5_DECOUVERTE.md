# Qualité indépendante — découverte sans Garmin

Base initiale : `9a2c5159f4b8ed351cf74cf293214938336ead90`.
Branche isolée : `codex/motionest-discovery-quality-5`.
Les fondations décrites dans `PREPARATION_PHASE_5_DECOUVERTE.md` ont été
réutilisées ; aucun nouveau moteur d'onboarding, d'identité ou d'historique.
Cet agent ne modifie aucun fichier applicatif.

## Huit tests indépendants sur les vraies routes

`tests/test_discovery_phase5_quality.py` utilise `DashboardHandler`, des sessions
signées réelles et des comptes vérifiés/non vérifiés synthétiques, avec tous les
fichiers et secrets de session dans un répertoire temporaire. Aucun bypass
d'authentification HTTP ; aucun compte ni jeton Garmin réel.

Les contrôles couvrent :

- POST de découverte anonyme refusé (401), compte non vérifié refusé (403).
- Choix explicite, répétable et propre au compte authentifié ; un `user_id`
  adverse dans le corps ne modifie pas le compte voisin.
- Aucune activité de démonstration ou faux jeton créé ; contexte historique
  vide et source sans Garmin explicites.
- Profil, entrées canoniques, mémoire et conversation comparés octet par octet
  avant/après activation et import tardif simulé. L'inspection locale et le
  runtime existants sont réellement exécutés ; seul le réseau/import Garmin
  est remplacé par un client et une métadonnée synthétiques.
- Sync et publication sans connexion refusées avant `connect`/setup.
- Compte déjà connecté toujours accessible ; ancien flag de découverte ignoré
  dans la source effective lorsqu'un jeton Garmin existe.
- Routes privées toujours interdites à l'anonyme, compte voisin isolé.
- Dix lectures initiales exactes du `Promise.all` de `loadAll`, toutes disponibles
  sans recommandation ni plan, après choix explicite de découverte.

Premier checkpoint : **7 réussis, 0 échec, 0 ignoré**, 10,85 s.
La régression de bootstrap ajoutée après le défaut navigateur échoue d'abord :
**1 échec, 7 désélectionnés**, 6,04 s. Ce n'est pas un succès ni un test ignoré.
Après correction A `2cf761d` : **8 réussis, 0 échec, 0 ignoré**, 10,14 s.
L'agent A a également rejoué les huit tests après son correctif UI `5dc694c` :
8 réussis en 15,22 s. Quatre warnings Pydantic préexistants seulement.

Commit des tests qualité : `8f91bdaf5e3a44d2d0c579b23d77c5214c3edf10`.
Preuves ignorées : `reports/phase5-quality-first.xml`,
`phase5-quality-boot-red.xml` et `phase5-quality-final.xml`.
La suite complète unique appartient à l'orchestrateur, sans exécution redondante
par cet agent et sans résultat présumé ici.

## Deux défauts trouvés par le parcours navigateur réel

1. **Bootstrap bloqué** après le choix sans Garmin : neuf lectures initiales
   répondaient 200, mais `/api/garmin/workout-delivery` répondait 500 à cause de
   `PlanProposalError(recommendation_unavailable)`. L'absence de recommandation
   est normale pour ce nouveau profil. A corrige le traitement typé en lecture
   (`unavailable`, 200), conserve les exceptions inattendues à 500 et ne crée
   aucun effet Garmin. La régression indépendante des dix routes passe après.
2. **Éditeur de profil complet bloqué à la première étape** : POST goal répondait
   200 avec `nextStep=complete`, et `indexOf('complete')` ramenait l'interface à
   l'étape zéro. A corrige la progression de cet éditeur et conserve la voie
   sans Garmin ; aucun formulaire ou onboarding parallèle n'est créé.

Les corrections sont faites par A, jamais dans les fichiers applicatifs de B.
Les anciens essais UI avec des réponses API entièrement factices ne pouvaient
pas établir la disponibilité des dix routes réelles ; le présent parcours le fait.

## Vérifications navigateur après correction

Skill `playwright-cli` et bibliothèque locale consultés précédemment, puis
réutilisés. Chromium headless, session propre `motionest-discovery-quality`.
Serveur sur `127.0.0.1` avec le vrai handler, véritables cookies signés et profils
temporaires ; `connect`, setup et lancement IA interdits par des garde-fous de
fixture. Aucune réponse API métier n'est remplacée par un JSON de succès fictif.

Parcours vérifié :

1. Compte Alice sans Garmin → dashboard redirige vers la page de connexion.
2. Bouton « Continuer sans Garmin » → dashboard chargé, bandeau explicite,
   rafraîchissement Garmin masqué, historique vide.
3. « Renseigner mon profil et mes préférences » → trois étapes parcourues,
   préférence modifiée puis sauvegardée ; rechargement confirmé par GET onboarding,
   préférence persistée et mode découverte conservé.
4. Quatre vues en **1440×900 et 390×844**, soit huit observations : zéro exception
   `pageerror`, pas de débordement horizontal sur les états mesurés.
5. « Connecter Garmin plus tard » ouvre réellement `/connect-garmin`, puis le
   dashboard reste accessible. Aucun import automatique, activités toujours vides.
6. Compte Bob avec jetons synthétiques stockés → dashboard accessible, bandeau
   découverte caché, rafraîchissement visible et profil accessible.

Console finale fraîche : **0 erreur, 0 warning**. Les deux corrections ciblées C
sont aussi visibles : favicon HTTP 200 et zones affichant « Aucune activité
importée : zones indisponibles. ». Le serveur synthétique a journalisé des
connexions abandonnées `WinError 10053` pendant les navigations rapides ; aucune
cause nouvelle n'est attribuée et aucun changement réseau n'est entrepris.
Cette réserve Windows ne remplace pas les résultats exacts des contrôles finaux.

Preuves ignorées : `reports/discovery_browser_server.py`,
`reports/discovery_browser_probe.js`, `reports/discovery-browser-matrix.json`,
`reports/discovery-mobile-390.png` (inspectée visuellement) et les snapshots/
logs `.playwright-cli/`. Les ports éphémères et cookies ne sont que ceux des
comptes locaux synthétiques. Navigateur dédié fermé et serveur arrêté après
la dernière preuve ; aucun service utilisateur n'est supprimé.

## Démonstration optionnelle et limites

Aucune démonstration synthétique interactive n'est ajoutée. Les fixtures de
développement ne sont pas un espace produit isolé. Une vraie démonstration
demanderait un namespace de données, un marquage permanent, une politique de
sortie/effacement et l'interdiction de publication distincts ; ce coût est
différé plutôt que d'introduire une seconde architecture dans cette mission.

Les viewports Chromium ne constituent pas une validation native Expo sur
Android/iOS. Le coach n'a reçu aucun appel IA réel par B : la campagne payante
synthétique, ses tokens et son résultat fonctionnel appartiennent au rapport
de l'orchestrateur. Aucun Garmin réel, push ou modification de `main` par B.
