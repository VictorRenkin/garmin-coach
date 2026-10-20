# Qualité indépendante — combinaison 3B / 3C / 4A

La correction 4A existante est réutilisée, sans réécriture des conversions,
synthèses ni projections. Base combinée : merge `c0de7d46` de `ea52e6b` et
`ebd2580`, puis arbre applicatif stabilisé `5230807`. La préparation documentaire
3D a ensuite été reprise dans `7cf16a6`, parent du présent livrable qualité.
Branche isolée : `codex/motionest-quality-numeric-4a`.

## Contrôles réutilisés et ajout ciblé

Les contrats et tests existants couvrent déjà durées 28/84 jours, secondes,
distances par discipline, données nulles/manquantes, recherche historique,
confidentialité, budgets, limites sportives et calendrier. Ils sont réexécutés
sans nouvelle implémentation ni affaiblissement des assertions.

Deux tests supplémentaires dans `tests/test_numeric_combination_quality.py`
traitent les interactions réellement manquantes :

- Le banc combiné parcourt les vrais chemins locaux de lecture historique,
  contexte dashboard, appel structuré du coach simulé, persistance conversationnelle,
  recherche 1.2B, application du patch et conversion SDK. Les six réponses de
  modèle sont **simulées**, avec zéro jeton consommé. L'import interrompu et son
  statut dans la recherche sont vérifiés. Le second tour relit l'échange conservé.
- Le validateur du banc refuse les erreurs numériques initiales et des distances
  attribuées à la mauvaise discipline. Il accepte aussi « n km en course », sans
  imposer un modèle rédactionnel unique ni autoriser le franchissement d'une autre
  discipline pour chercher la valeur attendue.

Le contrôle calendrier/accords/publication simulée de deux séances complexes
appartient aux tests existants et à l'agent 3D ; il n'est pas recopié ici.

## Tests ciblés

Première validation de la combinaison avant le correctif isolé 3D :
**134 réussis, 0 échec, 0 ignoré**, 41,87 s. Ce résultat intermédiaire ne valide
pas le correctif ultérieur de livraison.

Validation ciblée finale de l'arbre `5230807` avec le présent banc et ses tests :
**146 réussis, 0 échec, 0 ignoré**, aucune désélection, en **29,25 s**.
Quatre avertissements Pydantic préexistants du SDK tiers seulement.

Modules : `test_coach_numeric_presentation`, `test_numeric_combination_quality`,
`test_conversation_sport_context`, `test_conversation_history_search`,
`test_history_search_quality`, `test_calendar_3c`, `test_calendar_3c_quality`,
`test_sports_capabilities_2b`, `test_complex_double_day_3d` et
`test_delivery_confirmation_guard`.

Preuve : `reports/numeric-combination-targeted-final.xml` du worktree qualité.
La suite complète sera exécutée une seule fois par l'orchestrateur sur le SHA
final comprenant les documents et commits de livraison. Son résultat appartient
au rapport central ; il n'est pas anticipé ici.

## Six appels IA réels, nouvelle hypothèse d'interaction

Le banc `scripts/validate_numeric_combination.py` exige `--real`. Les appels ont
été autorisés et exécutés par l'orchestrateur après lecture du banc et validation
hors ligne. Plafond global de six appels, sans répétition de scénario ni relance
automatique (`max_retries=0`). Les profils, fichiers, historiques et clients Garmin
sont synthétiques, isolés dans des répertoires temporaires.

| Scénario | Contrôle fonctionnel obtenu |
|---|---|
| 37 800 secondes sur 28 jours | Réponse : 10 h 30 min 00 s ; moyenne 2 h 37 min 30 s |
| 109 800 secondes sur 84 jours | Réponse : 30 h 30 min 00 s ; moyenne 2 h 32 min 30 s |
| Triathlète | 12 km course, 60 km vélo, 3 km natation sur la semaine canonique ; aucune somme multisport annoncée |
| Double journée et import interrompu | Identités distinctes, 08 h / 16 h, total prévu 60 min ; recherche locale réelle sur 28 jours avec `import_status=interrupted` et couverture `incomplete_import` ; limites explicites dans la réponse |
| Trois blocs et récupérations 30 / 45 / 90 secondes | Sortie `modify_session`, indice 0 ; patch effectivement appliqué à un fichier temporaire, revalidé en `PlannedSession`, trois blocs conservés ; durée exécutable récursive du SDK = 1 920 secondes, égale à son estimation |
| Second tour | Historique conversationnel persisté relu ; 10 h 30 et 2 h 37 min 30 s repris correctement, 21 activités confirmées par les synthèses canoniques |

**6/6 succès techniques et 6/6 contrôles structurés réussis.** L'orchestrateur a
ensuite relu manuellement les six réponses et confirmé : pas de fatigue ou de
charge physiologique inventée, pas d'addition de distances hétérogènes, import
incomplet explicite, ni promesse de capacité sportive inexistante. Le succès
d'un appel technique seul n'a pas servi de verdict fonctionnel.

Modèle effectivement routé : **gpt-5.6-luna**, selon la configuration existante.
Consommation mesurée : **62 347 jetons d'entrée + 1 567 de sortie = 63 914**.
Six appels exactement ; aucune autre campagne 3B/3C rejouée.
Preuve : `reports/numeric-combination-real.json` dans le worktree qualité.

Le wrapper vérifie avant envoi l'absence des marqueurs privés synthétiques,
l'égalité du contexte canonique avant/après présentation, les identités et horaires
du calendrier, ainsi qu'un compagnon numérique inférieur à 4 096 octets. Le plus
grand payload complet de ces six appels fait **29 710 octets**. Ce relevé est
celui des profils mesurés ; il ne remplace pas les essais de budget maximal
réalisés séparément par l'agent d'intégration.

## Portée et limites

Cette livraison qualité ne modifie aucun fichier applicatif. La correction
minimale du défaut 3C découvert par l'agent 3D est documentée séparément dans
`VALIDATION_3D_HORS_LIGNE.md` et testée dans les modules de livraison concernés.
La réserve antérieure de perte d'accusé après upload n'est pas déclarée résolue.

Les horaires de Motionest ne deviennent pas des horaires transmis à Garmin :
le chemin actuel ne programme qu'une date. Les structures SDK, même exactes en
secondes, ne démontrent ni acceptation d'une API distante ni exécution sur montre.
L'incomplétude est correctement exprimée dans le scénario mesuré ; cela ne
constitue pas une garantie pour toutes les formulations futures du modèle.

Aucun appel Garmin réel, aucune donnée personnelle réelle, aucun push ni
modification directe de `main` par cet agent. Les sauvegardes et autres worktrees
restent sous le contrôle de conservation de l'orchestrateur.
