# Motionest — consolidation des phases 1.2A et 1.2B

État examiné le 15 octobre 2026, Europe/Brussels. Cette mission prépare une intégration, sans modifier main ni lancer la phase 2.

## Carte Git et états conservés

```text
68c704a — base commune, checkout original détaché avec travail local
├─ 13 commits de main ─ ba9465d (main et origin/main locaux)
└─ de7dcbd (sauvegarde source 1.2A + travaux locaux préexistants)
   └─ livraison 1.2B et tests/documentation ─ 969f6d4

ba9465d + 969f6d4 ─ d6cceb8 (fusion d’intégration isolée)
```

| Référence | État et relation |
|---|---|
| Checkout original `Documents/garmin-coach-main/garmin-coach-main` | HEAD détachée 68c704a ; changements locaux et fichiers non suivis conservés. Les 301 fichiers du manifeste du 14 octobre sont identiques à la sauvegarde. |
| `codex/backup-motionest-12a-20261014` | de7dcbd, enfant direct de 68c704a. Contient 15 fichiers ajoutés/modifiés, dont la phase 1.2A et des travaux préexistants. |
| `codex/motionest-phase-12b` | 969f6d4, descendant de de7dcbd ; sept fichiers de livraison 1.2B par rapport à la sauvegarde. |
| `codex/motionest-phase-12b-quality` | 449743f ; code et tests identiques à 969f6d4. Les sept patches de cette branche sont déjà présents dans la livraison sous des SHA équivalents. |
| `codex/motionest-phase-12b-docs` | f989326 ; patch documentaire repris sous b01b65f puis précisé par 969f6d4. Il ne faut pas le reprendre à nouveau. |
| `main` | ba9465d, dans `Documents/garmin-coach-github-website`. Treize commits depuis 68c704a : plans, parcours, dashboard et livraison Garmin. Aucun changement des phases 1.2A/B de la branche de livraison n’y était encore fusionné. |
| `origin/codex/coach-multisport-context-20261013` | b879595, autre proposition multisport, non intégrée à cette consolidation. Ne pas l’assimiler au contexte 14 jours et à la recherche validés. |
| `codex/motionest-integration-12ab` | Branche isolée fondée sur main, puis fusion de 969f6d4. Contient les deux parents par ascendance réelle, pas seulement des copies de fichiers. |

Les références distantes mentionnées sont les références locales disponibles ; aucun fetch ou push n’a été effectué.

## Déjà intégré / restant à intégrer

La branche de livraison contient déjà la phase 1.2A, la phase 1.2B, les tests qualité et la documentation. `git cherry` confirme l’équivalence des patches qualité/documentation. Les fusionner ou les cherry-picker une seconde fois serait inutile.

La branche d’intégration contient désormais les treize commits de main et toute la livraison 969f6d4. La fusion d6cceb8 a été réalisée sans conflit avec la stratégie Git ort ; seul le rapprochement de dashboard.py demandait une fusion automatique.

Le delta par rapport à main est de 21 fichiers avant ce rapport : aucune suppression. Le snapshot de sauvegarde inclut aussi des changements de marque mobile/README et des évaluations synthétiques préexistants ; ils sont conservés, pas présentés comme une nouvelle fonctionnalité historique.

Les fichiers `sites/` non suivis du checkout original ne sont pas intégrés : ils restent à leur place et les sources nécessaires sont dans l’archive du 14 octobre. Ils constituent du travail distinct. Aucune suppression, aucun reset ni clean n’a été utilisé.

## Variante locale non commitée dans le worktree main

Deux changements supplémentaires ont été découverts : `garmin_coach/dashboard.py` modifié et `tests/test_coach_multisport_context.py` non suivi. Cette variante transmet une autre clé `historique_multisport` avec cinq activités, alors que la livraison validée utilise `contexte_multisport`, quatorze jours et les synthèses 28/84. L’ajouter telle quelle réintroduirait deux projections et un risque de duplication.

Ces deux fichiers sont sauvegardés et vérifiés dans le checkout original sous `backups/consolidation-20261015/main-pending-sources.zip`. Le diff et les empreintes de départ sont conservés dans `main-pending.patch` et `initial-state.json`. Les fichiers d’origine restent intacts ; la variante n’a été ni effacée ni intégrée arbitrairement.

Options nécessitant une décision avant la modification de main :

1. Conserver cette variante sur une branche distincte pour revue, puis approuver la projection canonique déjà validée.
2. Identifier d’éventuels apports uniques de la variante et les adapter dans un travail ultérieur avec tests, sans ajouter un second contexte historique.

Aucun conflit Git non résolu n’existe dans la branche proposée. La variante non commitée reste un recouvrement fonctionnel à arbitrer, et ses tests ne font pas partie de la validation de l’intégration présente.

## Contrôles

Les modules Garmin, livraison, acceptation/proposition de plans, moteur de séances, configuration et comptes/sécurité (neuf fichiers contrôlés) sont identiques à main. Les changements de ces fonctionnalités déjà commis sur main sont donc conservés.

Les 21 fichiers ajoutés/modifiés ont été contrôlés : aucun chemin de secrets, données utilisateur, rapports locaux ou dépendances ajoutés ; aucune valeur de clé/jeton/secret/mot de passe configurée n’a été trouvée dans ces fichiers. Les tests de confidentialité et d’isolation des payloads complètent cette vérification, qui ne constitue pas un audit général de tout l’historique Git.

La validation utilise la suite Python configurée par pytest.ini. Les scripts manuels de capture navigateur ne sont pas présentés comme des tests exécutés. Aucun appel IA ou Garmin réel n’a été effectué pendant la consolidation.

## Résultats de validation

Deux passages complets ont été conservés, sans modification du code entre eux :

- Premier passage : 809 réussis, un échec, zéro ignoré, en 236,37 s. `test_logout_endpoints_expire_only_the_expected_session_cookie` a reçu `ConnectionAbortedError / WinError 10053` sur une connexion HTTP localhost. Aucune assertion métier n’a échoué dans ce test ; la cause exacte de l’interruption réseau n’a pas été établie.
- Relance ciblée de tout `test_multitenant_security.py` : 17 réussis, zéro échec, zéro ignoré, en 10,89 s.
- Second passage complet : **810 réussis, zéro échec, zéro ignoré, en 188,56 s**. L’incident réseau n’a pas été reproduit et aucune correction d’authentification ou de test n’a été appliquée.

Quatre avertissements Pydantic préexistants proviennent de garminconnect. Les fichiers JUnit `reports/consolidation-tests.xml`, `reports/consolidation-accounts-recheck.xml` et `reports/consolidation-tests-final.xml` restent locaux et ignorés par Git.

Détail de groupes extraits du JUnit final (sous-ensembles de la suite complète) :

| Groupe | Réussis | Échoués | Ignorés |
|---|---:|---:|---:|
| Contexte 1.2A et recherche 1.2B | 53 | 0 | 0 |
| Garmin et livraison | 26 | 0 | 0 |
| Proposition et acceptation des plans | 29 | 0 | 0 |
| Isolation et routage des comptes | 23 | 0 | 0 |
| Contrats et contexte historique | 126 | 0 | 0 |


## Procédure proposée après validation humaine

1. Relire le diff `main...codex/motionest-integration-12ab` et ce rapport ; vérifier les changements préexistants inclus dans de7dcbd.
2. Résoudre le statut de la variante locale du worktree main : la préserver sur une branche dédiée ou différer son adoption. Ne pas écraser dashboard.py et ne pas supprimer le test non suivi.
3. Vérifier que le worktree de main est propre et que main est toujours ancêtre de la branche d’intégration. Si main a avancé, reconstituer et revalider une intégration isolée ; ne pas forcer son pointeur.
4. Après autorisation, effectuer uniquement le fast-forward vers `codex/motionest-integration-12ab`. La fusion historique est déjà préparée ; aucun cherry-pick des branches qualité/documentation n’est nécessaire.
5. Garder les sauvegardes et les worktrees de revue jusqu’à validation finale. Aucun push n’est inclus dans cette procédure locale.

La mission actuelle n’exécute aucune de ces opérations sur main.

## État final des worktrees et point de reprise

| Worktree | HEAD / branche | État |
|---|---|---|
| `Documents/garmin-coach-main/garmin-coach-main` | 68c704a détachée | État local initial conservé, 28 entrées avec `-uall`, sources du manifeste inchangées. |
| `Documents/garmin-coach-github-website` | ba9465d / main | Modification dashboard et test non suivi conservés ; deux entrées, empreintes identiques au départ. |
| `motionest-history-12b` | 969f6d4 / codex/motionest-phase-12b | Propre, inchangé. |
| `motionest-quality-12b` | 449743f / codex/motionest-phase-12b-quality | Propre, inchangé. |
| `motionest-docs-12b` | f989326 / codex/motionest-phase-12b-docs | Propre, inchangé. |
| `motionest-integration-12ab` | codex/motionest-integration-12ab | Fusion testée d6cceb8 et rapport de consolidation dans un commit documentaire ; branche préparée pour revue. |
| `Documents/motionest-design-test` | 1a2d28b / codex/design-motion-test | Une entrée locale préexistante, hors périmètre ; aucun fichier touché. |

Aucun worktree ou point de sauvegarde supprimé. Les références main, livraison, qualité, documentation et sauvegarde ont été recontrôlées : elles sont identiques à celles enregistrées au début. Les changements supplémentaires de la mission sont uniquement la fusion isolée et ce rapport (22 fichiers dans le delta final par rapport à main).

Le SHA du commit documentaire final se lit avec `git rev-parse codex/motionest-integration-12ab` ; il n’est pas inscrit récursivement dans son propre contenu. La base combinée est prête pour validation humaine. La modification de main reste conditionnée à l’autorisation et à la mise à l’abri/décision sur sa variante locale. Aucun push ni développement de phase 2.
