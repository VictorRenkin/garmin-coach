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

## Travaux à poursuivre

Stabiliser B, intégrer séquentiellement les consignes A, puis regrouper sur la
branche de validation isolée. Réutiliser les tests existants et ajouter seulement
les régressions pertinentes. Les vrais appels IA éventuels seront synthétiques,
bornés, relus et mesurés ; aucune campagne ancienne rejouée sans nouvelle hypothèse.
Une seule suite complète sur l'arbre combiné final, sans présumer son résultat.
En cas d'interruption, relire ce document, les SHA et états locaux, conserver les
fichiers non commités identifiés et reprendre seulement ce qui reste.

Pas de push, Garmin réel, suppression ou reset destructif. Main reste inchangé.
La stratégie d'upload incertain est une proposition, pas une réconciliation
développée. Aucune reprise automatique permanente configurée par ce rapport.
