# Préparation du premier pilote Motionest Web

## Périmètre proposé et décision

Base vérifiée : main `839fd052d610445bad4c71e771a26524deb1cafb` propre.
Branche de préparation : `codex/motionest-pilot-preparation`, isolée de main.
Mission du 21 octobre 2026 : réserves restantes, preuves manquantes et correction
locale reproduite ; aucune reconstruction des phases 1 à 6.

Verdict candidat : **prêt sous conditions pour un pilote Web encadré**. Ce verdict
sera confirmé après la suite complète. Il ne signifie ni ouverture publique,
ni validation Garmin réelle, ni démonstration automatique ajoutée au produit.

Le périmètre minimal proposé est un petit groupe accompagné, parcours sans
Garmin, profils réels seulement après choix explicite du cadre de test. Les
comptes avec Garmin restent validés par simulation uniquement. Ce périmètre doit
être accepté avant lancement ; aucun participant recruté, compte réel créé,
serveur public ouvert, appel IA payant ou effet Garmin réalisé ici.

## Checklist à critères objectifs

Les statuts distinguent preuve de code, preuve synthétique et preuve d'exploitation.
Une simulation réussie ne clôture pas un critère exigeant un service réel.

| ID | Critère de passage | État et preuve disponible | Condition avant pilote |
|---|---|---|---|
| P01 | Branche candidate propre, ascendance main, diff limité, ciblés et suite complète sans échec | Base vérifiée ; ciblés root 159 réussis ; suite complète en cours | Clôturer les résultats et relire le diff avant décision d'intégration |
| P02 | Inscription, vérification, login/logout et routes privées traversés avec vraies sessions | Acquis en HTTP synthétique : `PARCOURS_WEB_PHASE_6.md`, tests sécurité ; transmissions e-mail/anti-robot simulées | Pour accès distant, prouver les e-mails et Turnstile dans l'environnement choisi |
| P03 | Sans Garmin : onboarding, préférences, profil, dashboard et coach sans historique accessibles | Preuves HTTP et navigateur phases 5/6, historique vide sans seed ; comptes séparés | Expliquer les données absentes et configurer le fournisseur IA si chat réel inclus |
| P04 | Connexion Garmin tardive simulée ne perdant ni profil ni conversation, reprise après erreur | Quatre nouveaux cas HTTP : MFA 409, auth 401, service 503, limitation 429, puis soumission explicite 202 | Ne pas présenter cette preuve comme authentification Garmin réelle validée |
| P05 | Historique multisport, proposition complexe/double journée, accord/refus individuels conservés | Réutilisation des contrats et tests 1–6, vrais raccordements HTTP et preuves navigateur | Planification/publication restent limitées aux capacités déjà annoncées |
| P06 | Erreur d'actualisation compréhensible, bouton récupérable sans retry automatique ni perte de l'affichage | Défaut reproduit puis corrigé ; deux nouveaux cas Node transport/non-JSON et tentative manuelle | Observer les incidents pendant le pilote ; conserver les traces si récidive |
| P07 | Aucun accès anonyme/croisé, secret public ou consentement contourné dans les parcours concernés | Tests sécurité/isolation existants inclus dans les ciblés ; messages d'erreur génériques nouveaux | Limiter les accès opérateur et ne pas partager secrets ou espaces de stockage |
| P08 | Un upload incertain reste bloqué ; ID courant confirmé toujours reprenable | 22 cas incertains et HTTP/UI déjà livrés ; code Garmin inchangé dans cette mission | Informer qu'aucune résolution n'est livrée ; pas de JSON manuel pour contourner |
| P09 | Sauvegarde récente des données du pilote et restauration répétée dans un espace séparé | Quatre archives de travaux CRC valides et anciens fichiers protégés ; aucune restauration opérationnelle de comptes du pilote démontrée | Définir emplacement, responsable et test de restauration avant premiers comptes réels |
| P10 | Environnement d'accès choisi et test de bout en bout sur cet environnement | Démontré en local loopback ; aucun hébergement public/TLS/proxy/e-mail réel validé ici | Choisir local accompagné ou distant ; valider la configuration du canal retenu |
| P11 | Coach configuré et limites de conseil expliquées aux participants | Campagne réelle antérieure phase 5 : 4 réussites fonctionnelles et 1 partielle, sans erreur factuelle relevée ; aucun nouvel appel ici | Accepter la réserve de suivi et prévoir revue humaine ; ne pas annoncer validation conversationnelle exhaustive |
| P12 | Support, signalement et critères d'arrêt définis | Procédure proposée ci-dessous et spécification Garmin livrée | Désigner responsable et canal ; arrêter la session concernée si exposition croisée ou effet distant imprévu |

Les archives historiques protègent les travaux de développement. Leur intégrité
CRC ne prouve ni sauvegarde des futurs comptes, ni restauration de leurs secrets,
ni continuité de service. Ces preuves ne sont pas confondues avec P09.

## Derniers défauts confirmés et corrections

### Actualisation après interruption

Le handler `refreshBtn` de `garmin_coach/dashboard_v2.html` n'avait qu'un
`try/finally`. Rejet fetch ou JSON illisible : bouton réactivé mais message
« Synchronisation… » conservé, avec promesse rejetée sans traitement.
Reproduction C avant correctif : deux nouveaux tests rouges ; les deux anciens
tests HTTP passent dans la même exécution. Un `catch` statique affiche maintenant
« Mise à jour interrompue. Tu peux réessayer depuis ce bouton. » ; aucune reprise
automatique. Le `finally` existant restaure le bouton. Le détail privé de
l'exception n'est pas affiché, les gardes/imports/métriques ne changent pas.

Les deux régressions exécutent le vrai handler extrait avec Node, vérifient une
seule requête, zéro rechargement à l'échec, historique affiché identique puis
tentative manuelle réussie. Elles ne prétendent pas prouver l'atomicité de
l'import ni une panne Garmin réelle. Rapport : `PILOTE_WEB_QUALITE.md`.

### Instructions de démarrage contradictoires

Le README présentait encore Garmin comme prérequis obligatoire et niait les
comptes multiples. Correction documentaire minimale : Garmin facultatif pour
la découverte, démarrage avec historique vide, espaces utilisateurs isolés,
fournisseur IA requis pour le chat et limites de l'hébergement public explicites.
Le parcours existant est décrit ; aucune fonctionnalité nouvelle n'est ajoutée.

### Nouveaux parcours prouvés, sans défaut source

A ajoute uniquement quatre récupérations après erreur Garmin tardive ; mêmes
routes et contrôles existants. Lectures dashboard/formulaire sans retry auto,
aucun setup avant nouvelle soumission explicite, profil/entrées/mémoire/messages
inchangés octet pour octet et Bob sans token d'Alice. Aucun changement backend.
Rapport : `PILOTE_WEB_PARCOURS.md`.

## Réserves maintenues et exploitation

`WinError 10053` reste un incident de transport historiquement observé, pas un
défaut d'authentification démontré. Un contrôle borné des deux anciens tests
`test_coach_status_and_config_http_endpoints_do_not_return_key` et
`test_logout_endpoints_expire_only_the_expected_session_cookie` passe. Aucune
modification d'authentification, d'assertion ou du banc n'est justifiée par cette
seule réserve. Les résultats historiques rouges restent documentés ; aucune
boucle de relance jusqu'au vert. Si l'incident réapparaît, conserver le premier
log et son contexte avant diagnostic, sans action Garmin ni effacement de données.

Quatre avertissements Pydantic du SDK Garmin persistent, comme sur la base ; pas
de montée de version ou refonte de dépendance engagée pour les masquer. Aucun
nouveau défaut de sécurité confirmé dans les raccordements examinés. Cela
réutilise les tests ciblés existants et ne constitue pas un audit général.

Pour un pilote local accompagné, conserver l'écoute loopback par défaut
(`dashboard.build_parser`) et le guide `README_LOCAL.md` ; son bypass d'e-mail
est exclusivement un mode de test local explicite. Pour un accès public,
l'opérateur doit vérifier le mode production, une URL HTTPS cohérente, les
cookies Secure, SMTP et Turnstile réels, et l'absence du bypass local. Sources :
`.env.example`, `account_security.production_mode/public_auth_config/verify_human/`
`send_account_email`, `user_accounts.session_cookie`, `DashboardHandler` et
`dashboard.main`. Aucune valeur de secret réelle n'est lue pour cette checklist.
Ces paramètres ne prouvent pas à eux seuls la sécurité du déploiement : celui-ci
et ses essais restent à autoriser et à réaliser. Ne pas simplement élargir le
host pour transformer le serveur local en hébergement validé.

La réserve conversationnelle phase 5 est conservée précisément : au suivi après
connexion synthétique, faits et calculs corrects mais un paragraphe au lieu de
deux et limites physiologiques moins rappelées. Pas de nouvelle campagne IA,
modification de prompt ou garantie 5/5 ajoutée. Source :
`SESSION_PHASE_5_UI_CIBLEE.md`, campagne et revue humaine antérieures.

## Garmin incertain : spécification, pas implémentation

`PILOTE_RESOLUTION_GARMIN_INCERTAINE.md` complète la stratégie historique avec
l'état actuel `upload_requested_at`. Le blocage persiste tant qu'une preuve
d'attribution suffisante manque. Existence, appartenance au compte et lien avec
la tentative sont trois faits distincts : nom, date, durée, capture, ID saisi ou
liste vide ne permettent pas d'adopter un objet ni de prouver sa non-création.

La procédure proposée informe et consulte en lecture seule ; ni utilisateur ni
support ne doit supprimer une intention ou modifier manuellement le store pour
forcer un renvoi. Aucune API d'adoption, déblocage, recherche distante ou
suppression livrée. Une future résolution devrait vérifier côté serveur les
droits du compte, l'identité exacte, l'accord actuel, le sport, la date et les
collisions ; le rôle administrateur ne vaut pas consentement du sportif.
Un propriétaire du système ayant accès aux fichiers reste un acteur de confiance,
pas un acteur techniquement neutralisé par une consigne de support.

Limites inchangées : verrou dans un seul processus, anciennes tentatives sans
trace indétectables, retries internes SDK non qualifiés, coupure électrique/fsync
non garantis, aucune garantie exactly-once distante. Réception sur montre et
horaires Garmin non prouvés. La compatibilité Garmin réelle n'est pas validée.

## Décisions utilisateur avant lancement, non exécutées

1. Accepter le périmètre initial sans Garmin réel, les participants et le niveau
   d'accompagnement ; ne pas confondre simulation du connecteur et preuve réelle.
2. Choisir accès local ou hébergement distant et autoriser séparément le
   déploiement ainsi que les essais e-mail/anti-robot/IA nécessaires. Aucun push
   ou nouvel abonnement/service n'est implicitement autorisé par ce rapport.
3. Désigner support et responsable des données ; fixer sauvegarde/restauration,
   durée du pilote et traitement des retours, sans collecter de secrets Garmin.
4. Décider séparément d'une qualification Garmin réelle et du chemin API retenu
   avant toute connexion/upload réel. Une éventuelle résolution exécutable exige
   une autre mission et des preuves suffisantes ; pas de déblocage manuel rapide.
5. Revoir la branche et autoriser une éventuelle intégration : main ne change pas
   automatiquement pendant la préparation.

Critères d'arrêt proposés : accès à un autre compte ou secret exposé, effet
Garmin inattendu, disparition de déclarations/messages confirmée ou erreur
répétée empêchant l'accès au profil. Suspendre le parcours concerné, conserver les
traces expurgées et diagnostiquer ; ne pas réessayer une création Garmin incertaine.

## Livraisons et preuves de reprise

| Responsabilité | Branche | Commit agent | Résultat |
|---|---|---|---|
| A parcours | codex/motionest-pilot-web-journeys | a8336edee1eaa5912b93a6aff7f76fe3634afe9d | 4 tests HTTP réussis, aucun backend modifié |
| B résolution | codex/motionest-pilot-uncertain-spec | bf2c55106391dc4bee573f57c16876f1702a4ee8 | Spécification seule, tests antérieurs réutilisés sans prétendre une preuve distante |
| C qualité | codex/motionest-pilot-web-quality | 23364472626d786ba12637bb9ad4dc196fe70d5e | 12 ciblés réussis, dont 2 nouveaux ; un catch UI |
| Root | codex/motionest-pilot-preparation | Code combiné afadd9ba10d95b25d5b87f902eb4050c12374b55 | 159 ciblés réussis ; suite complète en cours |

Revue indépendante A de la correction C et du README sur afadd9ba : aucun
blocage ; aucun changement de `dashboard.py` ou `garmin_delivery.py`, aucun retry
implicite. Les fusions conservent les ascendances sans cherry-pick en double.

Preflight : 24 empreintes originales hors natif identiques, quatre archives CRC
valides, variante `04aece083b9e307fa38aba94feab7ea417e00877` et 42 worktrees
préservés. Les anciennes références, notamment les livraisons Web 6 et son
rapport 97213fa, restent accessibles. Preuves ignorées locales :
`reports/pilot-preflight.json`, `pilot-combined-targeted.xml/log`,
`pilot-full.xml/log`. Les résultats finaux et l'état Git sont ajoutés en clôture.

En cas d'interruption, reprendre à partir des SHA et journaux conservés, sans
rejouer les étapes validées. Aucun mécanisme de relance permanent créé ; un arrêt
complet ne garantit aucune reprise automatique. Aucun accès ni changement natif,
appel Garmin réel, IA payante, nouveau connecteur, générateur, push ou suppression.
