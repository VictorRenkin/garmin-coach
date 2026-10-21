# Pilote Web — qualité ciblée et réserves HTTP

## Base et périmètre

Branche `codex/motionest-pilot-web-quality`, créée depuis `main` au commit `839fd052d610445bad4c71e771a26524deb1cafb`. Les anciennes branches sont préservées. Mission ciblée : réserves HTTP intermittentes et conséquences visibles d’une interruption, avec conservation des données. Aucun audit général, changement d’authentification, modification d’assertion métier ou nouvelle fonctionnalité.

Les preuves déjà validées sont réutilisées : suite complète phase 6 de **1 107 réussites**, contrôle réel navigateur des consentements et de l’incertitude Garmin (`docs/PHASE_6_INTERFACE_WEB.md`), contrôles de confidentialité et séparation des comptes déjà intégrés. Aucun nouveau navigateur nécessaire pour la reproduction déterministe du gestionnaire JavaScript de synchronisation. Aucun fichier de l’application native hors périmètre ouvert, audité, modifié ou testé.

## Réserve HTTP historique : ce qui est établi

Le rapport phase 5 conserve un échec initial `ConnectionAbortedError / WinError 10053` pendant le POST anonyme `/api/coach/config`, avant l’assertion de statut 401. Le GET précédent avait réussi. Le même test avait ensuite passé isolément sur la branche et sur sa base, puis une suite de confirmation avait passé 1 069 tests. Le problème de déconnexion antérieur avait déjà été corrigé dans son banc pour envoyer le POST sans corps utilisé par les vrais boutons ; ne pas réintroduire ce défaut de fixture.

Contrôle borné actuel, une seule exécution des deux anciens tests :

- `tests/test_dashboard.py::test_coach_status_and_config_http_endpoints_do_not_return_key` : réussi.
- `tests/test_multitenant_security.py::test_logout_endpoints_expire_only_the_expected_session_cookie` : réussi.

Cette exécution figurait avec deux nouvelles régressions volontairement rouges : total **2 réussis, 2 échoués, 4 avertissements SDK, 3,85 s**. Aucun échec HTTP actuel reproduit. Cela ne prouve pas la disparition future du transport Windows et ne permet pas d’en attribuer précisément la cause au réseau ou au serveur. Ni authentification ni banc HTTP ne sont modifiés sur la base de cette réserve.

## Défaut confirmé et correction

Dans `dashboard_v2.html`, le gestionnaire du bouton de mise à jour utilisait `try/finally` sans `catch`. Si `fetch` rejetait, ou si une réponse non JSON faisait échouer son décodage, le bouton redevenait actif mais le statut conservait « Synchronisation avec Garmin et analyse des nouvelles activités… ». La promesse rejetée n’était pas traitée. Symptôme trompeur : action terminée en erreur présentée comme encore en cours.

Reproduction sans réseau : les nouvelles régressions exécutent avec Node le vrai gestionnaire extrait du template, une requête interrompue puis une réponse non JSON. Avant correction : **2 échecs**, précisément sur la promesse rejetée. Aucun historique, secret, compte ou service réel n’est utilisé.

Correction minimale : un seul `catch` affiche « Mise à jour interrompue. Tu peux réessayer depuis ce bouton. ». Le `finally` existant conserve la restauration du bouton. Le message ne reprend pas le détail technique potentiellement privé de l’exception et ne prétend pas que Garmin a réussi ou échoué. Aucune requête supplémentaire automatique, aucun changement du backend, de l’import ou des métriques.

Les deux cas vérifient :

1. Absence de promesse rejetée et message explicite d’interruption.
2. Bouton réactivé avec son texte habituel.
3. Référence à l’historique affiché conservée, zéro rechargement après échec et une seule requête.
4. Nouvelle tentative manuelle possible : une deuxième requête réussit, son message est affiché et un seul rechargement est effectué.

Ce test ne prétend pas garantir l’atomicité du moteur d’import. Il vérifie uniquement le comportement de l’interface face à une interruption et sa récupération manuelle.

## Validation finale de cette branche

```text
pytest tests/test_pilot_web_quality.py tests/test_web_ui_phase6.py tests/test_ui_phase6_fixes.py -q --disable-warnings
12 passed, 4 warnings in 3.05s
node --check reports/web6-syntax.js : succès
git diff --check : succès
```

Soit deux nouveaux cas de pilote et dix contrôles UI déjà présents. Quatre avertissements de modèles Pydantic du SDK Garmin existant. Une première validation verte du même correctif avant ajout de la récupération manuelle avait également passé 12 tests en 3,58 s ; le total final ci-dessus inclut cette vérification supplémentaire. Aucun test désélectionné ou affaibli. La suite complète consolidée est à la charge de l’orchestrateur.

Fichiers livrés : `garmin_coach/dashboard_v2.html`, `tests/test_pilot_web_quality.py`, ce rapport. Aucun appel IA payant, Garmin réel, push ou changement de `main`. Aucun serveur ni navigateur ouvert pendant ce contrôle ; les serveurs synthétiques de la mission précédente avaient déjà été fermés.

## Critères et limites pour le pilote

Pour ce périmètre qualité, aucun blocage de confidentialité ou d’autorisation nouvellement démontré. Cela réutilise les validations précédentes et les contrôles spécifiques de l’orchestrateur, sans annoncer un nouvel audit de sécurité.

La livraison est candidate après succès de la suite consolidée, vérification de l’arbre final et traitement des prérequis d’exploitation par l’orchestrateur. Le pilote doit rester explicite sur les limites conservées : compatibilité Garmin réelle non démontrée, création incertaine sans résolution automatique, horaires Garmin et réception sur montre non confirmés. L’incident Windows historique reste une réserve de transport à surveiller, avec conservation des traces si réapparition ; aucune boucle de relance jusqu’au vert n’est justifiée.

Les preuves de rendu et de consentements précédentes restent applicables : la seule modification du présent correctif est le retour d’erreur du bouton de mise à jour. Les états critiques inconnus, les protections de renvoi et les données de séance ne sont pas modifiés.
