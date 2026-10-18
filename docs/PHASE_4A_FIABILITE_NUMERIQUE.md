# Phase 4A — Fiabilité numérique du coach IA

Base : main propre à `24fbf6c3d6b23eb521ae26058a5f7a0795a9f179`.
Branche isolée : `codex/motionest-numeric-4a`.

## Diagnostic ciblé

Les preuves `SESSION_LONGUE_PHASE_2A_20261015.md` et les rapports locaux
`motionest-freshness-2a/.../reports/conversations-2a*.json` montrent :
37 800 secondes interprétées comme 37 h 48 et des moyennes hebdomadaires erronées,
alors que les totaux canoniques sont corrects. Le triathlète reçoit aussi un total
65 puis 75 km issu de l’addition course/vélo/natation. Le document
`PREPARATION_PHASES_3_4.md` a été relu sans entreprendre ses autres périmètres.

Les valeurs sources sont déjà numériques et unitaires : WindowSummary porte
observed_duration_seconds et observed_distance_by_sport_km. RuntimeMotionestContext
transmet les fenêtres 28/84 jours ; la projection conversationnelle et la
recherche historique réutilisent `_window`. Le coach recevait ces secondes brutes,
avec seulement l’exemple 3600 secondes = une heure. Aucune durée lisible ni moyenne
hebdomadaire de durée déterministe ne l’empêchait de faire sa propre conversion.
L’addition des distances était une initiative du modèle, pas un agrégat du moteur.

## Réutilisation et correction minimale

`summary._duration` est le formatteur existant réutilisé (arrondi à la seconde
par le round Python existant, format h/min/s, sans remise à zéro après 24 h).
Aucun nouveau calcul d’historique, classement sportif ou moteur de confiance.

`coach_numeric_presentation.build_numeric_presentation` lit au plus six synthèses
existantes : 28 jours, 84 jours, deux semaines et deux périodes recherchées. Il
fournit les secondes observées et leur libellé, ainsi qu’une normalisation de
présentation par semaine : secondes observées / (jours calendaires inclus / 7).
Cette normalisation inclut les jours sans activité et ne réagrège jamais une liste
d’activités. Les valeurs absentes restent null ; zéro mesuré reste zéro. Sans dates
valides, pas de moyenne hebdomadaire inventée. Les secondes moyennes sont présentées
avec au plus six décimales ; les libellés utilisent l’arrondi du formatteur existant.

Exemples de référence :

| Période | Secondes observées | Durée lisible | Moyenne par semaine |
|---|---:|---|---|
| 28 jours | 37 800 | 10 h 30 min 00 s | 2 h 37 min 30 s (9 450 s) |
| 84 jours | 109 800 | 30 h 30 min 00 s | 2 h 32 min 30 s (9 150 s) |

L’ajout `presentation_numerique` est limité au payload du modèle, sur ses deux
chemins fournisseur. La projection canonique, ses budgets 48 000/24 000 octets,
ses troncatures et ses champs de couverture restent identiques. Le complément
ne contient ni liste d’activités ni distances ou charge recalculées. Ses six
entrées maximales restent sous 4 Kio dans la régression dédiée ; cette taille
est un surcoût au payload modèle, pas un changement des budgets canoniques.
Aucune nouvelle limite de cinq activités ni nouvelle projection multisport.

Les consignes demandent de reprendre les libellés fournis, de comparer les
moyennes calendaires pour des fenêtres différentes et de conserver les limites
des mesures partielles. Les distances par sport restent dans leurs champs
canoniques ; elles ne doivent pas être additionnées dans un total multisport.
Le volume temporel observé ne mesure ni charge physiologique ni fatigue.

## Tests synthétiques

Les régressions couvrent les secondes/heures, les totaux et moyennes 28/84 jours,
les semaines, une vraie période calendaire de 30 jours, zéro et absence de mesure,
les valeurs invalides, les dates invalides, les distances hétérogènes, les métriques
partielles, un import interrompu, l’identité des données canoniques envoyées et
l’absence de données privées supplémentaires. Les deux chemins fournisseur sont
capturés sans appel externe ; la présentation reste bornée à six synthèses.

- Tests ciblés finaux : **118 réussis, 0 échec, 0 ignoré** en 12,05 s.
- Suite complète : **860 réussis, 0 échec, 0 ignoré** en 190,54 s.
  Les 17 nouveaux cas portent le total de 843 à 860.
- Quatre avertissements Pydantic préexistants dans garminconnect.

XML : `reports/phase-4a-targeted-final.xml` et `reports/phase-4a-full.xml`.
La première passe ciblée à 117 tests réussis précède l’ajout du test de borne et
confidentialité ; aucun test retiré ou assertion métier affaiblie.

## Validation IA réelle mesurée

Le banc `scripts/validate_numeric_4a.py` réutilise FakeGarmin et les histoires
synthétiques de `validate_conversations_2a.py`. La date du faux rafraîchissement
est fixée au 15 octobre pour reproduire les mêmes faits, notamment 37 800/109 800 s.
Le script exige --real, utilise le fournisseur natif configuré, isole les comptes
dans des répertoires temporaires et vérifie l’absence des marqueurs privés dans
le payload. Aucun compte ni appel Garmin réel ; aucune clé copiée ou journalisée.

**5 appels réels réussis techniquement**, répartis entre quatre hypothèses :

- Confirmé : 10 h 30 et 30 h 30 ; moyennes 2 h 37 min 30 s et 2 h 32 min 30 s.
  Semaines 2 h 30 puis 3 h, cohérentes avec les secondes canoniques.
- Triathlon : course 12 km sur chaque semaine, vélo 50 puis 60 km, natation 3 km
  sur chacune. Durées 3 h puis 3 h 30 ; aucune somme kilométrique multisport.
- Sans FC/charge : durées et moyennes cohérentes ; aucune charge physiologique
  ni fatigue déduite des seules durées.
- Mesures partielles/import interrompu : durées observées 30 min puis 1 h et
  distances disponibles par sport conservées ; refus de conclure au volume réel.

La première formulation triathlon contenant « volume » a déclenché la clarification
historique locale avant toute requête IA : ce n’était pas un appel réel. La reprise
utilise exactement la formulation originale de phase 2A, sans changer le parseur.
Le premier seed incomplet pouvait terminer son faux import ; il a été rejoué avec
un état explicitement interrompu après le seed. Ces ajustements concernent uniquement
le banc synthétique. Aucun appel répété sans hypothèse nouvelle ; aucun nouveau
réglage du modèle, routage, budget de sortie ou fournisseur.

| Campagne | Appels IA | Tokens entrée | Tokens sortie | Total |
|---|---:|---:|---:|---:|
| Première passe | 3 | 25 658 | 910 | 26 568 |
| Triathlon original + import explicitement interrompu | 2 | 17 555 | 567 | 18 122 |
| Total | **5** | **43 213** | **1 477** | **44 690** |

Preuves locales : `reports/phase-4a-real.json`,
`reports/phase-4a-real-corrected-fixtures.json`,
`reports/phase-4a-qualitative-review.json`. Usage mesuré par le suivi existant.

## Réserves et périmètre d’arrêt

Le modèle reste probabiliste : cette validation confirme les cas numériques
ciblés, pas une garantie arithmétique sur toute phrase future. Aucun réécrivain
de réponse ou second appel correcteur automatique n’est ajouté.

Dans l’essai explicitement interrompu, le modèle refuse le volume réel complet,
mais dit encore que la liste d’activités est indiquée comme complète. Cette
ambiguïté de complétude déjà signalée en 2A reste documentée, sans traitement
4D. Les caractères de contrôle/formatage et autres explications de confiance ne
sont pas corrigés ici. Aucune phase 2C, 2D, 3, 4B, 4C ou 4D engagée.

## Fichiers et livraison

- `garmin_coach/coach_numeric_presentation.py` : formatage ciblé, sans accès disque.
- `garmin_coach/coach_chat.py` : complément dans le seul payload modèle et consignes.
- `tests/test_coach_numeric_presentation.py` : régressions déterministes.
- `scripts/validate_numeric_4a.py` : validation réelle bornée sur données synthétiques.
- Le présent rapport.

Aucun fichier Garmin, import, TrainingHistorySummary, DataConfidence,
runtime_context ou projection historique modifié. Main reste propre au commit de
base ; commit de livraison uniquement sur la branche isolée après validation.
Les archives historiques 1.2AB sont lisibles ; l’archive initiale 2B et ses cinq
empreintes SHA-256 sont vérifiées. Variante historique `04aece0` conservée.
Aucun worktree ni sauvegarde supprimé. Aucun push ni intégration automatique.
