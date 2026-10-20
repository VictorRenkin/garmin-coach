# Session — fiabilité des explications et lisibilité du coach

## État initial et reprise

Mission 74ac2332-3d45-41fa-a973-ef18fdc9477e, 20 octobre 2026.
Main propre à 4de172b0ac2cfd082ef4b3db04122329071d201c ; 4A et protections
3B/3C déjà intégrées. Aucun fast-forward vers main n'est autorisé automatiquement
pendant cette mission. La livraison combinée restera sur une branche isolée.

Protection : quatre archives CRC valides, cinq empreintes de sauvegarde 2B
conformes, 27 fichiers modifiés/non suivis originaux empreintés. Preuve main :
reports/explanations-preflight.json. Anciennes branches et variante 04aece0 préservées.

## Agents réels et propriété

Root coordonne, possède la branche codex/motionest-explanations-integration,
le banc conversationnel combiné éventuel, les tests finaux et ce rapport.
phase3_preparations assume A provenance 4C, codex/motionest-provenance-4c.
phase3b_quality assume B lisibilité 4D, codex/motionest-readability-4d.
phase3b_development assume C reprise Garmin en lecture seule,
codex/motionest-garmin-uncertain-strategy. Maximum quatre exécutions avec root.
coach_chat.py est réservé à B initialement ; A y travaillera seulement après
stabilisation de B et transfert explicite de propriété. Projections et tests
distincts permettent le travail parallèle sans écriture simultanée commune.

## Premiers constats confirmés

4C : la projection ordinaire avait activity_list_complete pour la liste locale
hebdomadaire, sans exposer les états d'import globaux. La recherche 1.2B exposait
déjà import_status/coverage_status. A réutilise load_history_import sans nouveau
moteur ni modification des métriques ou du stockage historique.
Checkpoint A 0a0b5707aa78341d873cf5a2aeed4b4da0f2518f : six lignes de projection,
huit cas de provenance et rapport. Ciblés : 49 réussis, zéro échec, zéro ignoré,
12,90 s ; passage précédent projection/numérique : 40 réussis, 10,03 s.
Les consignes du coach attendent la livraison B pour modification séquentielle.

4D : l'archive synthétique phase 2A contient des contrôles dans response.message
avant stockage/UI. Exemple 5 suivi de U+0019 puis 30 s : supprimer le contrôle
fabriquerait 530 s. Une archive 2B montre aussi une paire littérale de backslash-n
dans la prose avant affichage. Le JSON et la vue ne créent pas ces octets.
Le raw HTTP fournisseur n'étant pas archivé, on ne peut pas attribuer historiquement
le défaut au fournisseur plutôt qu'à son extraction SDK. La frontière observable
est la réponse structurée reçue. La validation explicite est préférée au décodage
aveugle, sans suppression des retours légitimes, chiffres ou Unicode utile.

C : document seul f0787ff3c27ca22ee210327f13b12ae309fb5ab9,
docs/STRATEGIE_REPRISE_GARMIN_INCERTAINE.md. Aucune modification de livraison.

## Livraison des agents et combinaison

B livré propre à d0b0e38ed757f8ceb7f38ba25c2ceb5fd952e661 : 29 nouvelles
régressions, 77 tests finaux réussis (3,05 s), aucun échec ou ignoré. Passage élargi
antérieur : 122 réussis, 20,50 s. Les caractères invalides sont refusés, jamais
supprimés au risque de fabriquer un chiffre. Les séquences littérales parasites
sont refusées seulement pour la forme de prose observée ; code et citations
restent littéraux. Le garde précède aussi le réparateur IA compatible existant.

A a fusionné B puis modifié les consignes seul :
8e794edbcf14014b6ea7ea3bfc30a4fff27e81f6, parents 0a0b570 et d0b0e38.
Les dix tests 4C vérifient aussi les deux chemins modèle. Combinaison ciblée :
162 réussis, zéro échec, zéro ignoré en 27,41 s. Aucun appel réel par A/B/C.

Root a fusionné C puis A+B sans conflit, en conservant leur ascendance :
6d6614bc2cb79773056e979c0595b0e3e7d9d5a9. Aucun cherry-pick en double.
La suite complète de ce code combiné est terminée ; résultat ci-dessous.
Le banc manuel scripts/validate_explanations_4cd.py a passé sa simulation sans
réseau, puis ses quatre appels synthétiques, zéro retry.
Les anciens scénarios 3B/3C/4A ne sont pas rejoués. Chaque appel doit réellement
atteindre le SDK ; une clarification locale n'est pas un succès IA technique.

## Réutilisation et périmètre exact

build_conversation_sport_context réutilise load_history_import, le runtime,
les fenêtres canoniques et le budget de 48 000 octets existants. Les consignes
réutilisent DataConfidence, scope/cutoff et la présentation numérique 4A.
generate_response/_validate_conversation_output et store_response portent
la validation de lisibilité, sans décodage ou transformation des nombres.
Le stockage, l'import, Garmin et les générateurs ne sont pas modifiés.

Fichiers applicatifs : garmin_coach/coach_chat.py et
garmin_coach/conversation_sport_context.py. Tests ajoutés :
tests/test_coach_provenance_4c.py et tests/test_coach_readability_4d.py.
Autres fichiers : scripts/validate_explanations_4cd.py, ce rapport,
docs/PHASE_4C_PROVENANCE.md, docs/PHASE_4D_LISIBILITE.md et
docs/STRATEGIE_REPRISE_GARMIN_INCERTAINE.md.

## Réserves et stratégie Garmin

L'archive brute HTTP n'existe pas : l'origine fournisseur contre extraction SDK
ne peut pas être attribuée plus précisément. La frontière prouvée précède
stockage et affichage. Aucun nettoyage rétroactif des anciennes conversations
ou actions appliquées. La détection des échappements est volontairement bornée
au défaut observé ; les cas techniques ambigus restent inchangés.
Les consignes ne garantissent pas toute formulation future du modèle.

Le document C propose une intention durable avant upload, l'arrêt en cas de
réponse sans ID, puis une inspection future bornée (3 pages de 100, 5 détails),
sans adoption automatique par nom ni preuve d'absence tirée d'une liste vide.
Il ne développe aucun mécanisme. L'idempotence existante après ID persisté ne
résout pas la perte de réponse avant persistance. Une mission distincte et des
preuves d'identité de l'API retenue seront nécessaires avant implémentation.

## Validation finale terminée

Suite complète unique : **1 051 réussis, 0 échec, 0 erreur, 0 ignoré**, 271,49 s.
Preuves : reports/explanations-full.log et reports/explanations-full.xml.
Quatre avertissements Pydantic préexistants du SDK garminconnect, aucun autre
échec. Les tests Garmin simulés, idempotence, propositions/acceptation,
sécurité/isolation et les régressions numériques sont inclus dans cette suite.
Le premier lancement shell n'avait pas démarré pytest faute de dossier reports;
le dossier a été créé puis la seule suite complète a été exécutée.

La suite a commencé sur 6d6614bc2cb79773056e979c0595b0e3e7d9d5a9.
Le banc manuel et le rapport ont été finalisés ensuite ; aucune source
applicative ni aucun test n'a changé pendant ou après cette validation.
Les 39 cas ajoutés (10 provenance + 29 lisibilité) expliquent le passage
exact de 1 012 à 1 051 tests. git diff --check est conforme.

IA terminée : simulation entièrement factice (socket réseau interdit) puis
quatre appels réels, chacun mesuré, zéro retry, profils temporaires synthétiques.
Les deux défauts du banc corrigés avant appels réels : argument max_retries
initialement dupliqué, et clarification locale initialement comptée à tort.
La formulation finale évite la recherche ambiguë et exige un appel SDK par cas.
Aucune correction applicative n'a été nécessaire après ces appels.

| Scénario | Technique | Fonctionnel après revue | Faits vérifiés |
|---|---|---|---|
| Import interrompu, liste locale complète | Réussi | Réussi | un enregistrement, 6 km / 40 min, confiance faible, pagination incomplète, pas de totalité réelle affirmée |
| Import terminé sur trois mois | Réussi | Réussi | cutoff 30 juin 2026, référence 30 septembre, complétude dans ce périmètre seulement, passé extérieur inconnu |
| Historique local vide | Réussi | Réussi | absence locale distincte de l'absence réelle, import interrompu, aucune mesure inventée |
| Second tour persisté | Réussi | Réussi | trois messages au contexte, 6 km / 40 min conservés, deux paragraphes réels, vélo et emoji Unicode, limites conservées |

Modèle effectivement routé : gpt-5.6-luna. Entrée : 36 140 tokens, dont
16 080 cachés ; sortie : 1 044 ; total : 37 184. Les compteurs factices ne sont
pas ajoutés. Les charges utiles envoyées mesurent 14 819, 14 805, 14 105 et
16 044 octets (mesure record_usage). La comparaison de présentation numérique
et de contexte canonique passe ; le compagnon numérique reste sous 4 096 octets.
Aucun marqueur privé synthétique (titre, email, latitude/longitude) n'est envoyé.
Les réponses ont action=none et plan_modifie=false ; aucun effet Garmin.
La revue distingue ces quatre hypothèses réussies d'une garantie universelle.
Preuve ignorée : reports/explanations-4cd-real.json (usage et revue détaillés).
Le banc reproductible est commité à 0289e0ebe1229bf3b0e7d11a4d550342f44acb6f.

Conservation finale déjà contrôlée : 27 empreintes identiques, quatre ZIP CRC
valides, variante 04aece083b9e307fa38aba94feab7ea417e00877, 34 worktrees.
Main demeure propre à 4de172b0ac2cfd082ef4b3db04122329071d201c.
Mission terminée ; aucune validation supplémentaire ni développement automatique
n'est lancé. La livraison attend la revue sur sa branche isolée.

## Références et worktrees conservés

| Branche | Livraison |
|---|---|
| main | 4de172b0ac2cfd082ef4b3db04122329071d201c, inchangé |
| codex/motionest-provenance-4c | 8e794edbcf14014b6ea7ea3bfc30a4fff27e81f6, propre |
| codex/motionest-readability-4d | d0b0e38ed757f8ceb7f38ba25c2ceb5fd952e661, propre |
| codex/motionest-garmin-uncertain-strategy | f0787ff3c27ca22ee210327f13b12ae309fb5ab9, propre |
| codex/motionest-explanations-integration | code combiné 6d6614b, banc 0289e0e, rapport final ajouté ensuite |

Les 34 worktrees restent présents. Les anciens états sales restent ceux du
checkout original détaché 68c704a (16 entrées Git, 27 fichiers empreintés) et du
worktree design (une entrée). Les nouveaux worktrees A/B/C sont propres.
Preuves d'inventaire ignorées : reports/worktrees-final.json et
reports/preservation-final.json. Les fichiers sites/ originaux sont inchangés.
Prochaine étape sûre : revue de la branche combinée et de ses réserves, puis
intégration locale dans une mission explicitement autorisée ; aucune ici.

Pas de push, Garmin réel, suppression ou reset destructif. Main reste inchangé.
La stratégie d'upload incertain est une proposition, pas une réconciliation
développée. Aucune reprise automatique permanente configurée par ce rapport.
