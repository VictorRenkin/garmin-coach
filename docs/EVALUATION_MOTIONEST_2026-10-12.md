# Motionest — évaluation élargie du 12 octobre 2026

Cette campagne combine des vérifications du logiciel, six appels au coach IA de Motionest et une simulation MiroFish avec quatorze sportifs fictifs. Les résultats techniques et les réactions simulées sont séparés. Aucun avis de personne réelle, aucune efficacité sportive et aucune validation médicale ne sont établis par cette campagne.

## Périmètre exécuté

| Travail | Résultat |
| --- | --- |
| Suite de tests existante du projet | 711 tests réussis, 4 avertissements ; 174,76 secondes |
| Jeux de données synthétiques | 14 profils, 518 activités, importés dans des espaces locaux distincts |
| Contrôles de stockage et contexte | 56 contrôles réussis : dédoublonnage, comptage, disciplines et contexte subjectif |
| Contrôles HTTP | 92 réussis : 84 lectures d'API et 8 validations de saisie ou d'accès |
| Isolation et confiance | 18 contrôles réussis, dont absence de fuite entre comptes, historique vide, ancienneté et cardio absent |
| Représentation des entraînements | 12 contrôles réussis ; les limites sont testées comme refus attendus, pas comme fonctionnalités prises en charge |
| Coach IA réel de Motionest | 6 réponses obtenues avec `gpt-5.6-sol`, mode approfondi ; aucune action exécutée |
| MiroFish / OASIS | 14 profils nommés, 5 tours, 70 tours d'agents, 68 commentaires générés, 39 réactions, 70 consultations du fil |
| Messages initiaux de simulation | 14 messages scriptés, distincts des 68 commentaires générés |
| Navigateur | Connexion, première utilisation, choix d'objectif, validation de chrono et passage vers Garmin ; vues bureau et mobile à 390 × 844 |

Les 178 contrôles ciblés sont des vérifications du comportement observé : certains confirment volontairement un refus du logiciel. Leur réussite ne signifie donc pas que tous les besoins des quatorze sportifs sont couverts. Les appels HTTP locaux ont pris au maximum 402,6 ms pendant ce test ; cela ne constitue pas un benchmark en production.

## Profils et couverture

| Profil fictif | Activités | Sports conservés | Confiance historique | Besoin décisif |
| --- | ---: | --- | --- | --- |
| Alex Martin, professionnel | 144 | Course, renforcement | Haute | 12 séances, doubles journées, répétitions et récupérations précises |
| Samir Laurent, reprise du football | 12 | Football, course, renforcement | Faible | Charge des entraînements collectifs et changements de direction |
| Léa Moreau, triathlon | 96 | Natation, vélo, course, renforcement | Haute | 8 séances, transitions, puissance vélo et allure natation |
| Emma Bernard, débutante | 9 | Course, marche | Faible | Consignes simples et progression lisible |
| Hugo Simon, reprise de course | 13 | Course, marche | Moyenne | Distinguer anciennes performances et niveau actuel |
| Julie Petit, trail | 50 | Course, randonnée, renforcement | Haute | Dénivelé, effort et sorties longues |
| Thomas Leroy, peu disponible | 20 | Course | Haute | Deux créneaux variables et reports expliqués |
| Nora Dubois, sans cardio | 24 | Course, vélo | Moyenne | Ne pas inventer les zones ni la récupération |
| Lucas Roux, gêne déclarée | 24 | Course, renforcement | Moyenne | Conserver le contexte subjectif sans diagnostic |
| Inès Robert, sans historique | 0 | Aucun | Aucune | Comprendre le bénéfice avant la connexion Garmin |
| Marc Fontaine, données rares | 2 | Course, vélo | Faible | Distinguer valeur absente, zéro et estimation |
| Sarah Michel, historique ancien | 24 | Course, vélo | Moyenne | Ne pas utiliser huit mois d'ancienneté comme preuve récente |
| David Henry, coach humain | 60 | Course, renforcement | Haute | Conserver le plan existant et contrôler les changements |
| Manon Olivier, hausse de volume | 40 | Course, vélo, renforcement | Haute | Expliquer charge et déclarations de récupération séparément |

Les mesures, dates et personnes sont inventées. Une structure d'intervalles du profil professionnel est adaptée de [Haugen et al., Sports Medicine - Open, 2022](https://doi.org/10.1186/s40798-022-00438-7), qui décrit notamment des répétitions de 1 000 m avec récupération de 90 secondes. L'adaptation est un cas de représentation technique ; elle n'est pas un journal Garmin réel ni un programme à suivre.

## Ce qui fonctionne dans le périmètre testé

1. **Le contexte sportif conserve les disciplines.** Le football reste `soccer`, la natation et le vélo restent distincts de la course. Le stockage multisport est donc utile même si le plan actuel est centré sur la course.
2. **Le réimport ne duplique pas les activités.** Les 518 enregistrements sont conservés et les quatorze comptes restent séparés dans les API testées.
3. **Les données manquantes et anciennes ne sont pas toutes traitées comme fiables.** Aucun cardio n'est inventé dans le profil sans capteur ; l'historique vide a une confiance nulle et les activités anciennes ne deviennent pas des activités récentes.
4. **La gêne déclarée est conservée.** Le coach confirme sa présence, demande une précision et n'exécute aucune adaptation pendant l'essai.
5. **Le coach fait preuve de prudence sur plusieurs cas.** Il ne déduit pas de récupération chiffrée sans mesures et ne prend pas les performances vieilles de huit mois comme des allures actuelles.
6. **Les validations de formulaire et d'API testées répondent correctement.** Un type de course absent, des minutes égales à 60, une date invalide, une distance négative ou une session invalide sont refusés. Après un objectif valide, l'écran de connexion Garmin apparaît.
7. **Les séances de course simples peuvent être converties en structure Garmin locale.** Les payloads de deux cas acceptés sont conservés, sans téléversement ni programmation sur un compte Garmin.

## Points prioritaires révélés par les tests

### 1. Le coach conversationnel ne reçoit pas tout le contexte multisport

**Constat vérifié.** `coach_context()` charge `load_public_activities()`, qui filtre les activités de course. Son contenu ne comprend pas le contexte canonique `athlete_state` / `training_history_summary`. Les fixtures du footballeur contiennent bien `soccer` et du renforcement, mais le coach répond que son contexte ne montre que quatre activités de course. La triathlète a des activités vélo et natation dans le moteur, tandis que le coach ne les voit pas dans les données récentes présentées.

**Conséquence.** La description « coach multisport » dépasse les informations effectivement données à la conversation. Le coach ne peut pas expliquer correctement une charge vélo, natation ou football qu'il ne reçoit pas.

**Source.** `garmin_coach/dashboard.py`, fonction `coach_context`, et `data/evaluation-motionest-20261012/coach-context-gap.json`. Réponses conservées dans `native-coach/football.json` et `native-coach/triathlon.json`.

**Proposition.** Fournir le contexte canonique multisport, les mesures disponibles et les limites de confiance au coach, puis ajouter une vérification où une activité de football ou de vélo est effectivement citée avec sa provenance. Cela ne doit pas, à lui seul, faire annoncer un plan multisport.

### 2. Un historique multisport ne signifie pas un plan multisport

**Constat vérifié.** Le type d'objectif `triathlon` est refusé par `GoalV1` et par l'API. Le formulaire propose des distances de course, du trail ou une autre distance. `PlannedSession` ne représente pas une discipline vélo, natation ou football, et le générateur produit un `RunningWorkout`.

**Conséquence.** Motionest sait lire des activités de plusieurs sports, mais l'essai ne démontre pas un programme football ou triathlon exécutable. Le coach testé reconnaît cette limite et n'a publié aucun plan.

**Proposition.** Clarifier le périmètre dans l'interface dès le départ. Si le triathlon et le football sont des objectifs du produit, prévoir leurs contrats propres, leurs blocs et leurs contraintes avant de les annoncer.

### 3. Les professionnels et certaines séances détaillées ne tiennent pas dans les contrats actuels

**Refus mesurés.** Une fréquence de 12 séances pour le professionnel, 8 pour la triathlète, une sortie de 30 km, 15 répétitions, une durée totale de 180 minutes ou une récupération de 1,5 minute sont rejetées par les contrats testés. Les limites présentes sont 7 séances hebdomadaires, 12 répétitions, 25 km et 150 minutes ; les phases utilisent des minutes entières.

**Sources.** `availability.py`, `training_cycle.py`, et `workout-results.json`.

**Réponse IA à surveiller.** Le coach distingue correctement les conseils textuels de l'exécution et reconnaît que 90 secondes ne rentrent pas dans le champ entier. Cependant, sa phrase indiquant que le format peut « décrire 15 répétitions » est ambiguë : le champ structuré les refuse. Sa réponse ne donne pas clairement toutes les limites de 7 / 12 / 25.

**Proposition.** Exposer des capacités explicites et cohérentes au coach et à l'interface. Décider séparément de la prise en charge des doubles journées, du nombre de séances et de la précision en secondes / mètres ; ne pas tronquer silencieusement les entraînements importés.

### 4. Le bénéfice avant Garmin reste difficile à explorer

**Constat navigateur.** Les comptes synthétiques avec un historique local sont dirigés vers la connexion Garmin. Sur l'écran testé, aucun bouton de démonstration ou de poursuite avec les données locales n'est affiché. Un compte neuf peut renseigner son intention, puis arrive lui aussi à cette connexion.

**Conséquence hypothétique.** Un utilisateur sans montre ou souhaitant comprendre le produit avant de donner accès à Garmin peut s'arrêter à cet endroit. Aucun abandon réel n'a été mesuré.

**Proposition.** Un accès à une démonstration clairement fictive permettrait de montrer la valeur. Les droits d'accès réels et le parcours Garmin doivent rester distincts de ce mode d'essai.

### 5. La confiance gagnerait à être plus explicite par composant

**Constat vérifié.** Le moteur conserve la distinction entre historique, état actuel, cardio et données manquantes. L'historique ancien peut avoir une confiance historique moyenne tout en n'ayant aucune activité récente.

**Réactions simulées.** Plusieurs profils demandent une explication de ce contraste, l'origine des valeurs et la différence entre une donnée mesurée et un ressenti déclaré.

**Proposition.** Afficher clairement « historique disponible » et « niveau actuel non établi », avec la dernière date et les éléments manquants. Ne pas remplacer toute la confiance par une note unique.

### 6. Petites frictions observées

Le navigateur demande `favicon.ico` et reçoit une réponse 401. Cela produit une erreur console, sans empêcher les parcours testés. Les écrans mobiles testés ne débordent pas horizontalement : document de 390 px et fenêtre de 390 px ; le dialogue d'objectif mesure 354 px. Ces observations ne constituent pas un audit complet d'accessibilité.

## Avis simulés : ce qu'ils apportent réellement

Les 68 commentaires proviennent de quatorze personnages, avec le même modèle IA et des données définies pour ce test. Ils ne sont ni des avis clients indépendants ni une validation de marché. Chaque profil a commenté : les réactions vont de la simplicité souhaitée par la débutante à la fidélité d'un plan professionnel, en passant par le report, la séparation des sports et le contrôle du coach humain.

Un commentaire peut dire qu'un comportement « n'est pas encore vérifié » alors qu'un test logiciel distinct le confirme : les agents ne reçoivent pas les résultats de tous les contrôles exécutés après le démarrage. Cette assertion doit être recoupée avec les résultats techniques, jamais transformée en défaut établi.

Les exemples et attentes sont consultables dans `AVIS_SIMULES.md`, généré à partir des commentaires bruts. Les échanges complets et les données de test sont conservés dans le dossier local d'évaluation.

## Changements réalisés

Les mentions visibles « Garmin Coach » correspondant au produit sont remplacées par « Motionest » dans le README, la documentation mobile, le nom de l'application mobile, son message de connexion, le service de santé HTTP et les messages du dashboard / pont Codex. Les intégrations Garmin Connect gardent leur nom. Le module Python et les identifiants techniques existants restent compatibles.

Le script `scripts/evaluate_motionest.py` permet de recréer les profils et l'espace isolé. Il importe les métadonnées synthétiques, mesure leur contexte et propose un serveur d'inspection local. Les écarts fonctionnels listés ci-dessus sont documentés ; cette campagne ne les implémente pas tous.

## Données de Massimo et limites restantes

Aucun compte, export ou jeu d'activités identifiable comme celui de Massimo n'a été retrouvé dans ce projet ni dans la copie locale `garmin-coach-github-website` inspectée. Aucun compte Garmin inconnu n'a été ouvert. La question du chemin de ses données et de leur utilisation reste en attente ; aucun historique de Massimo n'a été envoyé à OpenAI ou Zep.

Cette campagne ne valide pas une synchronisation Garmin réelle, un téléversement de plan, des enchaînements sportifs réalisés ni les effets d'un programme. Les appels au coach ont produit des réponses uniquement : aucune action n'a été appliquée. La phase de génération complète d'un plan doit être évaluée séparément sur des contrats correctement adaptés aux sports concernés.

## Reproduction

```powershell
.venv\Scripts\python.exe scripts/evaluate_motionest.py --output data/evaluation-motionest-nouveau
.venv\Scripts\python.exe scripts/evaluate_motionest.py --output data/evaluation-motionest-nouveau --serve
```

Utiliser un dossier neuf pour une nouvelle campagne. `--remeasure` relit les comptes synthétiques existants. Le serveur d'inspection n'écoute que sur `127.0.0.1:8778`, avec données et secrets de test séparés ; il ne modifie pas les espaces sportifs réels. Les jeux de données ne sont pas présentés comme des imports Garmin authentiques.

MiroFish : projet `proj_394b53e46c8c`, graphe `mirofish_ad511186966c4d2e`, simulation `sim_a0174827c7af`. L'ontologie définit exclusivement quatorze `AthleteProfile`. Les biographies ont été explicitement corrigées en personnes individuelles, car le générateur MiroFish traite sinon ce type inconnu comme une organisation. Les cinq tours sont répartis sur la journée et les quatorze profils sont actifs ; la fréquence obtenue n'est donc pas une mesure d'usage naturel. Le script Reddit autonome n'exporte pas les journaux attendus par le suivi MiroFish : les traces SQLite ont été conservées et exportées sans inventer de numéro de tour individuel.
