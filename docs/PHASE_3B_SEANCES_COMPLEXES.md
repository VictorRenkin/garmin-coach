# Phase 3B — Séances de course à blocs distincts

Base : `85937a6ebda0072a88f2ae2e3e5db9bc2b652edd`, phase 3A intégrée
localement par l'orchestrateur. Développement isolé sur
`codex/motionest-complex-sessions-3b`. Aucune intégration de 3B dans main.
La livraison 4A reste indépendante. Aucun appel Garmin réel ni push.

## Écart confirmé et composants réutilisés

La proposition V2 et la séance legacy représentaient un seul bloc homogène,
avec échauffement, répétitions, récupération et retour au calme. Le SDK installé
possède déjà `ExecutableStep`, `RepeatGroup` et une liste ordonnée d'étapes dans
`WorkoutSegment`. Aucun nouveau moteur de planification ou de livraison.

Les deux pipelines existants sont conservés :

- `generate_plan_proposal` → `PlanProposalV2` → validation locale et stockage →
  acceptation explicite de l'empreinte exacte → `publish_accepted_session` ;
- `PlannedSession` → patch conversationnel confirmé →
  `dashboard.modify_planned_session` → conversion legacy existante.

Les deux convertisseurs réutilisent le même constructeur pur des étapes de blocs,
`structured_workouts.build_block_steps`, construit à partir des helpers du SDK.
Il ne lit ni compte, ni historique, ni réseau et ne publie rien.

## Extension compatible du contrat

`session_blocks.SessionBlock` décrit un bloc successif :

- `effort_minutes`, positif, au plus 360 minutes ;
- `recovery_minutes`, de zéro à 120 minutes ;
- `repetitions`, de 1 à 100 ;
- `intensity_instruction`, texte qualitatif normalisé ;
- `effort_hr_zone` et `recovery_hr_zone`, cibles optionnelles explicites de 1 à 5.

Les minutes restent l'unité publique existante ; chaque durée positive doit
correspondre à au moins une seconde entière. Les valeurs négatives, non finies,
booléennes ou sous-seconde sont refusées. Une zone de récupération sans
récupération positive est refusée, plutôt qu'ignorée silencieusement.

La liste optionnelle `blocks` est partagée par `PlanSessionStructureV2`,
`PlannedSession` et `SessionPatch`. Les anciens JSON sans ce champ restent lisibles.
Les séances simples conservent leurs champs et leur chemin de conversion.
Les blocs structurés V2 restent réservés à `sport=running`.

Les champs du bloc homogène et la liste de blocs sont exclusifs. Le backend
supprime explicitement les trois anciennes valeurs lors d'un patch vers `blocks`.
Le retour vers une séance homogène exige les trois champs ensemble ; un patch
partiel ou contradictoire est refusé avant écriture. La date et les identifiants
existants ne sont pas modifiables par ce patch.

La somme des répétitions de tous les blocs reste au plus 100 et la durée exécutée
au plus 480 minutes : les plafonds existants ne sont pas multipliés par le nombre
de blocs. La liste elle-même est bornée à 100, ce qui ne permet pas de dépasser
le plafond cumulé. La durée exacte est :

`échauffement + Σ répétitions × (effort + récupération) + retour au calme`.

La récupération suit chaque effort, y compris le dernier. La durée annoncée
reste entière en minutes ; l'écart admis est au plus 30 secondes, correspondant
à l'arrondi à la minute. Les étapes exécutables conservent les secondes exactes.
La conversion SDK revalide la structure, y compris après `model_copy` non validant.

Les blocs imbriqués, les cibles en distance et les allures numériques ne sont pas
ajoutés. Leurs champs inconnus sont refusés. La présence d'un type récursif dans
le SDK ne vaut pas validation d'un nouveau contrat Motionest.

Le digest existant du prompt V2 change avec ses nouvelles consignes : les
propositions en cache peuvent devenir `stale` selon les règles existantes.
Elles doivent être préparées et acceptées à nouveau ; aucune acceptation ancienne
n'est transférée automatiquement vers un contenu différent.

## Fidélité de la conversion et de la prévisualisation

Chaque bloc devient un groupe répété ou des étapes successives, dans son ordre.
Une récupération positive est aussi exportée lorsque `repetitions=1` ; les deux
anciens adaptateurs l'omettaient alors malgré son inclusion dans la durée calculée.
Les nouvelles régressions comparent la durée des étapes réellement exécutables
avec `estimatedDurationInSecs`, et pas seulement deux estimations textuelles.

Les cibles FC explicites réutilisent le ciblage Garmin existant. Aucune zone
n'est déduite de mots comme « tempo », ni d'une allure. Sans cible numérique,
le SDK conserve `no.target` et l'interface annonce une consigne qualitative sans
cible Garmin chiffrée. La consigne complète reste locale ; la sérialisation SDK
ne démontre ni réception API ni affichage sur une montre.

`dashboard_v2.html` affiche chaque bloc, ses répétitions, ses durées à la seconde,
ses cibles et la récupération après la dernière répétition. Les deux vues V2 et
legacy utilisent ce même rendu de blocs.

L'adaptation globale legacy (`lighter`, `progressive`, `recovery`) ne recalculait
que les champs homogènes. Avec des blocs, elle pouvait changer la durée annoncée
sans changer les étapes, ou ajouter un champ homogène contradictoire. Elle refuse
désormais explicitement les plans ayant des séances complexes futures avant toute
écriture, avec une invitation à modifier la séance bloc par bloc. Les adaptations
des plans simples sont conservées. Aucun moteur d'adaptation complexe n'est ajouté.

## Validation déterministe et indépendante

Les données sont synthétiques et la livraison utilise exclusivement
`VerifiedClient`. Les contrôles couvrent la génération V2, la validation et le
stockage, le refus avant acceptation, l'acceptation explicite, la conversion locale,
un seul upload simulé lors d'une répétition, l'isolation, les dates et running-only.

Le fichier qualité indépendant `tests/test_complex_sessions_quality.py` vérifie
notamment les ordres d'étapes et de cibles, les limites cumulées, les copies non
validées, les conflits de représentation et les métriques non représentables.
Le test 3A refusant le champ `blocks` devient le refus des blocs incomplets ;
l'assertion d'absence d'écriture reste présente.

- Premier checkpoint applicatif : **135 réussis, 0 échec, 0 ignoré**, 7,20 s.
- Banc IA entièrement hors ligne, faux transport : **1 réussi**, cinq cas simulés.
- Correction de précision et qualité : **52 réussis, 0 échec, 0 ignoré**, 6,86 s.
- Derniers ciblés après refus d'adaptation globale : **151 réussis, 0 échec,
  0 ignoré**, 8,68 s.
- Suite complète intermédiaire sur `c09f242`, avant les deux derniers garde-fous :
  **924 réussis, 0 échec, 0 ignoré**, 226,62 s.
- Suite complète finale sur l'arbre applicatif et de tests `e260f409` :
  **929 réussis, 0 échec, 0 ignoré**, 223,87 s. Quatre avertissements Pydantic
  préexistants dans le SDK Garmin. Aucun incident HTTP pendant ces deux suites.
  Preuve : `reports/phase-3b-final-full.xml` dans le worktree qualité
  `motionest-complex-sessions-quality` ; la branche de livraison contient le même arbre
  applicatif et de tests, avec le présent rapport supplémentaire uniquement.

Les premières passes de développement ont corrigé des fixtures : argument
keyword-only, date de fixture passée et récupération nulle avec cible encore
présente. Elles ne sont pas comptées comme validations vertes. Un cas adversarial
réel, `effort_minutes=1e-10`, passait une tolérance flottante puis devenait zéro
seconde : le validateur refuse maintenant toute durée positive arrondie à zéro.
Deux régressions de développement couvrent effort et récupération infimes.

## Cinq appels IA réels mesurés

`scripts/validate_complex_sessions_3b.py` exige `--real`, utilise uniquement des
comptes temporaires synthétiques, force `max_retries=0` et plafonne les appels au
nombre de scénarios sélectionnés. Il ne passe jamais de client Garmin réel.
Le banc est lui-même exécuté hors ligne avec un faux transport dans les tests.

La génération V2 réelle passe par `generate_plan_proposal`, son stockage local,
ses validations et l'acceptation. Les trois patches legacy sont persistés et
revalidés, convertis, puis leurs blocs rejoués dans V2 et acceptés avant une
livraison uniquement simulée. L'appel refusé doit laisser le plan inchangé.

| Scénario | Succès technique | Succès fonctionnel | Tokens |
|---|---|---|---:|
| Génération V2 de deux blocs distincts, récupérations 30/90 s | Oui | Oui | 2 502 |
| Patch legacy, intensités FC distinctes | Oui | Oui | 5 395 |
| Patch legacy avec récupération de 45 s | Oui | Oui | 5 346 |
| Refus explicite de blocs imbriqués sans modification | Oui | Oui | 4 877 |
| Modification d'une séance déjà composée de blocs | Oui | Oui | 5 476 |
| Total | **5/5** | **5/5** | **23 596** |

Consommation : **21 035 tokens d'entrée**, **2 561 de sortie**, cinq appels API
exacts. Le succès fonctionnel compare les nombres de blocs et répétitions,
efforts, récupérations, zones explicites, dates conservées, durées exécutables,
stockage, acceptation et idempotence. Il ne repose pas uniquement sur le texte.
Les corrections ultérieures sur les durées infimes et l'adaptation globale sont
vérifiées de manière déterministe ; elles n'imposent pas de refaire ces appels.

## Vérification navigateur synthétique

Playwright CLI a ouvert le HTML de production avec toutes les routes interceptées
et des fixtures locales, sans compte ni serveur Garmin. La séance de vérification
contient trois blocs et des récupérations de 45, 30 et 90 secondes ; les cibles
FC 3 puis 4 sont suivies d'un bloc qualitatif sans cible chiffrée.

Le navigateur vérifie l'ordre échauffement/blocs 1–3/retour, les secondes exactes,
les zones et la mention des limites qualitatives, dans les vues V2 et legacy.
La fixture SDK associée représente exactement 2 250 secondes (37 min 30 s),
annoncées 38 minutes. Console : **0 erreur, 0 avertissement**. Aucune requête
d'écriture ni requête externe. La session nommée est fermée après vérification.

Preuves ignorées par Git dans le worktree de développement :
`reports/phase-3b-ui-snapshot.yml`, `phase-3b-ui-blocks.png`,
`phase-3b-ui-fixtures.json`, `phase-3b-ui.js` et `phase-3b-real.json`.

## Fichiers et limites de livraison

- `garmin_coach/session_blocks.py` : contrat commun et validation numérique.
- `garmin_coach/structured_workouts.py` : construction pure des étapes SDK.
- `garmin_coach/plan_proposal.py` : extension optionnelle V2 et consignes.
- `garmin_coach/training_cycle.py` : extension compatible legacy et consignes.
- `garmin_coach/coach_chat.py` : patch et consignes de représentation.
- `garmin_coach/dashboard.py` : bascule explicite du patch, refus d'adaptation globale non représentable.
- `garmin_coach/dashboard_v2.html` : prévisualisation des blocs et limites de cible.
- `garmin_coach/garmin_delivery.py`, `garmin_coach/garmin_workouts.py` : adaptateurs existants réutilisant la construction commune.
- `tests/test_session_blocks_3b.py`, `tests/test_complex_sessions_quality.py`,
  `tests/test_session_limits_3a.py` : développement, qualité indépendante, compatibilité.
- `scripts/validate_complex_sessions_3b.py` : banc IA borné et mesuré.
- Le présent rapport.

Pas de nouvelles doubles journées, de nouveau générateur, de changement d'import,
d'historique ou de synchronisation. Les contrats de dates, sept séances maximum,
acceptation explicite, isolation et protections Garmin restent en place.
Les anciennes branches, sauvegardes et la variante historique sont conservées.
Les limites réelles de l'API Garmin et des montres ne sont pas vérifiées.
Les préparations 3C/3D et fournisseurs figurent dans des documents séparés ;
aucun développement de ces phases n'est inclus.
