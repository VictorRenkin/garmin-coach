# Phase 6 Web — interface et parcours supplémentaires

## Périmètre et méthode

Base : `ee41c9e822c79060afa837f7f66c6db08e69dbf9`, branche `codex/motionest-web-ui-phase6`.
Le skill Playwright CLI a été lu après consultation de la bibliothèque locale et de ses métadonnées. Aucun fichier de l’application native hors périmètre n’a été ouvert, audité, testé ou modifié.

Les preuves de phase 5 (parcours découverte, édition du profil, favicon et zones vides) restent réutilisées. Les nouveaux essais concernent les séances complexes, les doubles horaires, les décisions unitaires dans la première semaine et le nouvel état d’upload incertain. Aucune capture identique n’a été refaite sans hypothèse nouvelle.

Banc jetable : vrai `DashboardHandler`, sessions signées, comptes vérifiés synthétiques, répertoires temporaires et stores de proposition/acceptation réels. Les fixtures existantes `pair`, `source_for` et `isolated` sont réutilisées. L’entrée de génération et le stade du parcours sont explicitement contrôlés pour afficher `dashboard` ou `review_plan` ; ce n’est pas une nouvelle validation de la génération ou du moteur de parcours. Connexion Garmin, démarrage d’import et file IA interdits par des gardes qui lèvent une erreur. Aucun compte ou réseau Garmin réel, aucun appel IA.

## Défauts et corrections minimales

| Constat reproduit | Preuve avant correction | Correction |
| --- | --- | --- |
| Un refus individuel est persisté mais la carte affiche le message générique d’absence d’accord. | Clic soir, POST 200 puis GET `sessions[evening].status=refused` ; texte inchangé. | `sessionDecisionMarkup` annonce « Séance refusée », aucun envoi, possibilité explicite de revalidation. |
| Les boutons unitaires dans la modale `review_plan` sont inertes. | Quatre `onclick=null`, clic matin sans POST et statut restant `missing`. Les cartes sont créées après la liaison précédente. | `bindPlanSessionActions` réutilise les actions existantes après le rendu du parcours. Retour de décision recherché dans la carte du bouton, avec attribut `data-session-status`, au lieu d’un ID dupliqué entre carte cachée et modale. |
| Le nouveau payload `unknown/uncertain` serait affiché « Pas encore créé » avec bouton d’envoi. | Exécution du vrai renderer : régression rouge sur le contrat public communiqué par C. | Label « Création incertaine », note prudente sur un précédent envoi, aucun bouton d’envoi ; garde du gestionnaire contre un ancien bouton avec état incertain en cache. Les identifiants confirmés conservent la vérification du calendrier. |
| Après erreur POST, la carte conserve l’ancien état jusqu’au prochain chargement. | Régression rouge : `loadAll` jamais appelé dans le catch. | Relecture bornée du dashboard après échec, sans second POST ; erreur initiale conservée. Si la relecture échoue, pas de boucle ni nouvelle tentative automatique. |

Le backend et les contrôles de consentement/livraison ne sont pas modifiés par cette livraison. Le refus côté serveur reste indispensable. La preuve des états Garmin incertains est une simulation d’interface, pas une preuve de compatibilité Garmin réelle.

## Vérifications navigateur supplémentaires

- Deux séances complexes à 08:00 et 16:00, décalage `+02:00` affiché ; détail des blocs, répétitions, cibles FC et récupérations de 30, 45 et 90 secondes conservés.
- Cartes chargées à 1440, 390 et 320 pixels : aucun débordement horizontal, aucune exception JavaScript.
- Accord du matin seul : POST réel, statut `partially_accepted`, matin accepté, soir sans accord ; seul le matin expose l’envoi.
- Refus du soir : POST réel, matin restant accepté et soir refusé ; nouveau texte explicite.
- Après correction de la modale : clic réel matin → POST 200 ; clic soir → refus persisté. Modale large de 960/354/284 pixels aux largeurs 1440/390/320, action globale visible et aucun débordement.
- Demande d’ajustement vide : focus sur le champ, aucune décision inventée.
- Vue coach avec compte synthétique connecté, sans historique : navigation accessible, pas de débordement, aucun appel IA. Les états découverte et l’édition complète du profil réutilisent les preuves phase 5 ; ils ne sont pas annoncés comme nouvellement exécutés.
- Contrat d’upload incertain injecté seulement sur GET livraison : à 390 et 320 pixels, label explicite, zéro bouton d’envoi, aucun débordement. Capture `reports/web6-uncertain-320.png` inspectée.
- Transition contrôlée : seule la route livraison est simulée, POST 409 `upload_uncertain`, puis GET `unknown/uncertain`. La carte se met à jour immédiatement, sans bouton d’envoi. La console contient une ressource HTTP 409 attendue, aucune exception JavaScript ; ne pas présenter ceci comme zéro erreur console.

Les essais ne remplacent ni la campagne réelle de routes backend d’A ni la campagne d’incertitude de C. Les modales utilisent les routes de consentement réelles ; la projection initiale du stade de parcours est une fixture explicite.

## Régressions et résultats

`tests/test_web_ui_phase6.py` ajoute sept tests exécutant les fonctions JavaScript de production avec Node : refus visible, upload incertain sans renvoi, ID confirmé et calendrier, refus sans action de livraison, liaison tardive et retour local de carte, relecture après erreur, garde d’ancien bouton.

Résultats intermédiaires volontairement conservés : première campagne 2 échecs / 2 réussites ; nouvelle régression modale 1 échec / 4 réussites ; nouvelle régression transition 1 échec / 5 réussites. Ils ont motivé les corrections, pas une désélection.

Validation finale ciblée :

```text
pytest tests/test_web_ui_phase6.py tests/test_ui_phase6_fixes.py -q --disable-warnings
9 passed, 4 warnings in 5.16s
node --check reports/web6-syntax.js : succès
```

Les deux tests existants couvrent favicon public/protection et absence de zones anciennes ou chargement permanent. Les quatre avertissements proviennent des modèles Pydantic du SDK Garmin existant. Aucune suite complète lancée par B : l’orchestrateur valide l’arbre consolidé.

Preuves locales ignorées : `reports/web6-matrix-output.json`, `reports/web6-review-fixed.json`, `reports/web6-uncertain-output.json`, scripts et sorties Playwright associés. Les cookies synthétiques temporaires ne sont pas livrés. Navigateur `motionest-web6` fermé et serveur synthétique arrêté après les essais.

## Livraison et limites

Fichiers : `garmin_coach/dashboard_v2.html`, `tests/test_web_ui_phase6.py`, ce rapport. Aucune refonte, aucun changement d’import, contrat sportif, générateur ou métrique. Aucun push, aucune modification de `main`, aucune suppression de sauvegarde.

Le rapprochement automatique d’un upload inconnu reste hors de cette correction UI. L’absence de débordement aux tailles testées ne constitue pas une validation sur téléphone physique. La suite consolidée et les tests d’isolation globaux sont rapportés séparément par l’orchestrateur.
