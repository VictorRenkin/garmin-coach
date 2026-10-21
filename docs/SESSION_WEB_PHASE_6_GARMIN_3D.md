# Consolidation Web phase 6 et fiabilité Garmin 3D

## Mission et limites

Base main propre vérifiée : ee41c9e822c79060afa837f7f66c6db08e69dbf9.
Branche isolée : codex/motionest-web6-integration. Aucun fast-forward main prévu.
Mission du 21 octobre 2026, pièce jointe 9cc6724a-c88e-48fe-8099-c95139e04bf3.
La pièce jointe s'arrête littéralement à « 6. Vérifier si une intention ».
Une question asynchrone demande la suite ; aucun texte manquant n'est inventé.
Les parcours web sont entièrement spécifiés et avancent indépendamment.

Après lecture de la stratégie existante et reproduction du risque, root retient
dans l'objectif général explicitement autorisé de sécurité Garmin une protection
conservatrice : intention persistée avant upload, arrêt des relances incertaines,
aucune réconciliation, réémission automatique, suppression ou compte Garmin réel.
Cette interprétation reste limitée à l'objectif écrit, pas à une permission
déduite de l'absence de réponse à la question. Toute suite reçue sera réconciliée.

mobile-app/ est strictement hors périmètre : aucun fichier natif ouvert, audité,
modifié ou testé. La recherche dans les seuls tests Python existants n'a trouvé
aucune référence mobile-app/ à exécuter. Pas de refonte des phases déjà validées,
nouveau connecteur, générateur, push ou suppression de sauvegardes/worktrees.

## Protection et propriété

Les 24 empreintes originales hors mobile sont inchangées ; les trois chemins
natifs du manifeste antérieur sont volontairement exclus des lectures de contenu.
Quatre archives de sauvegarde CRC valides et variante 04aece0 préservée.
Main reste ee41c9e propre. Preuve ignorée : reports/web6-preflight.json.

A phase3_preparations possède le backend web et dashboard.py ; B phase3b_quality
possède HTML/JS/CSS ; C phase3b_development possède garmin_delivery.py et les
tests de livraison ciblés. Chacun travaille dans un worktree distinct depuis main.
Les tests nouveaux et documents ont des noms distincts. Aucun fichier commun
n'est modifié simultanément ; interfaces de helper/statuts coordonnées.
Root possède ce rapport, la consolidation et les tests finaux. Quatre agents max.

## Checkpoints et constat initial

C checkpoint ae692f38f0c8b033d1ab29bf534eb211a7c51685 : deux caractérisations
fictives, ID absent et timeout après création distante simulée. Deux tentatives
actuelles → deux créations, zéro programmation et store vide. Ce comportement
est une preuve du défaut, pas le contrat final attendu. Aucun app modifié à ce point.

A a reproduit avant correction un nouveau trou du vrai parcours HTTP :
discovery opt-in puis GET connexion 200, mais POST connexion tardive 409 car
la garde n'accepte pas stage=dashboard en découverte. Aucun client réel appelé.
Correction backend minimale et régression avec profil/conversation préservés en cours.

## Point de reprise

Terminer les parcours A/B, protection C et leurs régressions, consolider uniquement
les corrections prouvées, revue indépendante et secrets, contrôles navigateurs
pertinents puis suite complète sur le code stable. Les vrais appels IA ne sont
pas nécessaires sans nouvelle hypothèse conversationnelle ; fournisseurs simulés
pour les parcours. Aucun résultat de test final n'est anticipé ici.
Conserver SHA/branches et preuves en cas d'interruption ; pas de relance permanente.

## Livraisons consolidées

A : 33b24fc22ec4294f2858562fddf625a4cbc42d72, branche
codex/motionest-web-journey-6. Connexion tardive HTTP désormais autorisée seulement
depuis la découverte explicite sans token ; compte non vérifié, compte déjà
connecté et étapes non autorisées restent refusés. Préflight canonique C appelé
avant connect, après contrôles existants de session/identité/accord.
Huit cas HTTP nouveaux : inscription/vérification/session/onboarding/coach vide,
connexion tardive et profil/mémoire préservés, données multisports, proposition
complexe/double journée, accords unitaires, livraison et réponse perdue simulées.
Matrice des vingt étapes et limites : PARCOURS_WEB_PHASE_6.md.
62 ciblés réussis (21,10 s), puis 104 sécurité/contexte réussis (20,85 s).

C : 6c27f9dd7869d0a814fee40f3c4e291ab028ac87, branche
codex/motionest-garmin-upload-intent. Même store/schema extensible, intention
upload_requested_at sauvegardée après validation et avant POST ; ID confirmé
ensuite persisté avant programmation existante. Une relance incertaine échoue
avant lecture calendrier/upload/schedule et avant connexion via la route web.
Les IDs invalides laissent également cette intention. Le getter expose unknown /
uncertain avec message statique ; une ancienne intention ne prête pas ses dates
ou événements à la séance courante. Le consentement, les métriques, les blocs,
horaires et l'idempotence d'ID confirmé restent les mécanismes déjà présents.
22 tests du module final réussis (6,71 s), passes élargies antérieures conservées.

B : 048154368fc9c05aff3211b88db8d5e4bc7d18f6 puis complément
b4540105716b8efcce98959f4a9c090c08a2f955, branche codex/motionest-web-ui-phase6.
Quatre défauts nouveaux prouvés : refus individuel indiscernable d'une absence
de décision, boutons unitaires inactifs dans une modale reconstruite, création
incertaine présentée comme non créée, et carte non rafraîchie après erreur.
Correctifs HTML locaux : refus lisible, binding commun/scopé à la carte rendue,
état incertain sans bouton de renvoi, relecture après échec sans nouvel upload.
Une reproduction complémentaire de rendu direct après éditeur confirme le même
défaut de binding ; insertion au point commun showJourneyProduct et régression
de deux rendus successifs. Dix ciblés finaux réussis, dont huit nouveaux et deux
régressions antérieures favicon/zones ; syntaxe JavaScript valide.
Rapport : PHASE_6_INTERFACE_WEB.md.

Les fusions sont réelles, sans cherry-pick en double. Tests combinés root :
**97 réussis, zéro échec, erreur ou ignoré**, 35,67 s, quatre avertissements SDK
Pydantic préexistants. Preuves : reports/web6-combined-targeted.xml et log.
Un test HTTP de présentation ancien remplaçant déjà connect/publisher remplace
aussi le nouveau boundary, avec assertion d'ordre preflight→connect→publish.
Les tests métier de réponse perdue utilisent le vrai helper ; aucun garde n'est
neutralisé. Deux attentes 3D visent désormais l'écriture d'ID plutôt que celle
de l'intention, et vérifient l'intention persistée après ID absent.

## Limites de sécurité explicitement conservées

Un ID reçu/persisté reste réutilisable. Sans ID, l'incertitude bloque la même
identité stable même après révision. Une intention legacy sans identité bloque
conservativement les nouvelles créations du compte, sans empêcher la reprise
d'un ID courant confirmé ni toucher un autre compte. Aucune résolution, adoption
par nom, recherche distante, effacement ou renvoi automatique n'est livré.
Une interruption entre intention et POST peut donc bloquer sans création réelle.

Les anciennes tentatives sans record ne peuvent pas être reconstruites. Le verrou
est intra-processus ; ni concurrence entre processus, ni fsync/coupure électrique,
ni retries internes du SDK/transport, ni exactly-once serveur ne sont garantis.
La nouvelle garantie est le blocage applicatif observé hors ligne et persisté,
pas une compatibilité Garmin, une preuve sur montre ou un engagement distant.
