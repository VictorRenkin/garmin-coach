# Phase 5 et corrections UI ciblées — suivi de livraison

Base main vérifiée : 9a2c5159f4b8ed351cf74cf293214938336ead90.
Mission 212a24be-f29e-4d79-9f4c-76c0416e59f0, 20 octobre 2026.
Main ne sera pas modifié. Branche de validation : codex/motionest-phase5-integration.

## Coordination et protection

27 empreintes originales inchangées, quatre archives ZIP CRC valides, variante
04aece0 et anciennes branches préservées. Preuve : reports/phase5-preflight.json.
Le checkout original sale et sites/ ne sont pas utilisés pour le développement.

Agents réutilisés, maximum quatre avec root : A phase3_preparations développe
le parcours sans Garmin ; B phase3b_quality possède les tests indépendants et
la vérification navigateur ; C phase3b_development corrige seulement favicon
et état vide des zones. C possède initialement dashboard.py/dashboard_v2.html,
puis les libère explicitement pour A ; aucune écriture simultanée commune.
Root possède ce rapport et le banc IA synthétique combiné.

## Critères et périmètre

Choix explicite d'accès sans source pour un compte Motionest autorisé, profil et
objectif existants réutilisés, aucune activité fictive, limites du coach visibles,
connexion Garmin ultérieure préservant les déclarations et la mémoire. Routes
protégées et consentement Garmin conservés. Démo optionnelle seulement si isolable
sans architecture nouvelle ; aucune démo ne doit polluer un historique réel.

## Point de reprise

Reprise après interruption de quota, explicitement demandée par l'utilisateur.
Main reste 9a2c515 et les 27 empreintes originales sont revérifiées identiques.
C a livré 0777edb2d9a9851af619d3dc97207b71e0eb1534 : favicon public et
état vide des zones, 50 ciblés réussis, zéro échec/ignoré. Fichiers dashboard
libérés explicitement puis fusionnés par A à 0149542, après backend 9967e07.
Le checkpoint A est propre. B reprend tests indépendants et navigateur.

Le banc scripts/validate_discovery_phase5.py a passé cinq simulations sans
réseau par C : retries=0, profil conservé, conversation 1→3→5 sur le même
compte objectif/connexion tardive/suivi. Les compteurs simulés ne sont pas des
tokens réels. Assertions de source et activité 0→1 renforcées ensuite par root;
nouvelle simulation nécessaire après combinaison. La connexion tardive du banc
est synthétique (_tokens+seed), pas un essai du service Garmin.

Développements isolés A/C et préparation qualité B en cours. À faire : revue et
fusion séquentielle C→A, consolidation des tests B, ciblés, navigateur desktop et
mobile simulé, quelques appels IA synthétiques mesurés, suite complète unique sur
code combiné stable, contrôle secrets/diff/statuts et rapport final.
Ne pas rejouer les validations précédentes ; aucun appel Garmin réel, push,
suppression, reset destructif ou mécanisme permanent de relance.

## Développement consolidé et validation en cours

A backend/UI livré à 1c928557b9559ccd4b8162aa9188dcf39bebbb18, rapport
63561c6e4603f9d3b9e69ab1819b1d35d5e1b75a. C est inclus par vraie fusion,
sans doublon ni édition simultanée des fichiers. Root a réuni ces travaux.
Parcours : compte vérifié, choix explicite via POST protégé, indicateur dans
onboarding existant, dashboard et éditeur de déclarations existants, coach
configuré, lien de connexion tardive. Aucun historique de démonstration ajouté.
Le drapeau est ignoré quand des tokens existent : import/erreurs Garmin continuent
de suivre le parcours antérieur. Les actions Garmin restent explicites et gardées.

B a reproduit un défaut bloquant avec les dix GET réels du bootstrap : absence
de recommandation → PlanProposalError → HTTP 500 sur workout-delivery. La
régression indépendante était rouge avant correction (1 échec, 7 désélectionnés,
pas 7 ignorés). Fix A 2cf761dd97c34cf1e9f340750fd4d629948f09be, intégré root :
la route GET traite l'erreur métier typée comme unavailable/sessions vides,
sans toucher au moteur de livraison ni masquer les exceptions inattendues.
28 tests ciblés A après fix réussis. Les tests indépendants et le navigateur
doivent confirmer ce chemin avant le lancement de la suite complète.

## IA synthétique — résultats réels

C a validé cinq simulations sans réseau sur le banc renforcé. Les cinq appels
réels suivants ont chacun atteint le SDK, sans retry ; comptes/données temporaires,
aucun Garmin réel. Connexion tardive simulée par tokens fictifs et seed local,
pas par le service distant. Les vrais endpoints restent couverts par B.

| Cas | Technique | Revue fonctionnelle |
|---|---|---|
| Aucun historique | Réussi | Réussi : intention débuter distincte d'une observation, douleur inconnue, pas d'inactivité réelle déduite |
| Profil déclaratif | Réussi | Réussi : mardi/jeudi/samedi, 3 séances préférées, musculation 2 × 30 min déclarées, pas de fatigue inventée |
| Objectif sans activité | Réussi | Réussi : 10 km déclaré, capacité/allure/niveau inconnus, aucun plan ni publication |
| Apparition d'activité après connexion simulée | Réussi | Réussi : 6 km / 40 min, moyennes 28 j = 10 min et 84 j = 3 min 20, objectif distinct, confiance faible |
| Continuité après connexion | Réussi | Partiel non bloquant : faits et calculs conservés, mais un paragraphe au lieu de deux et limites physiologiques peu rappelées |

Root et C ont relu les réponses indépendamment : aucune affirmation factuelle
fausse, aucune action structurée indue. Ne pas annoncer 5/5 pleinement fonctionnels.
Le dernier cas conserve date/fréquence d'objectif inconnues mais ne reprend pas
toutes les limites de niveau/charge/récupération dites au tour précédent.
Pas de relance aveugle ni modification des consignes 4 pour cette variabilité isolée.

Usage réel : gpt-5.6-luna, 45 766 tokens entrée dont 21 440 cachés, 1 395 sortie,
**47 161 total**. Les compteurs factices ne sont pas ajoutés. Contexte source
false→true et activités 0→1 vérifiés, même profil et conversation 1→3→5 conservés.
Aucun titre privé, email, coordonnée ou jeton synthétique dans le payload envoyé.
Preuve ignorée : reports/discovery-phase5-real.json avec revue et usages détaillés.
Banc reproductible : scripts/validate_discovery_phase5.py, commit 4bfd5fa.

## Stabilisation UI et qualité indépendante

B a livré les huit tests indépendants dans 8f91bdaf5e3a44d2d0c579b23d77c5214c3edf10,
parent 2cf761d, réellement fusionnés sur la branche combinée. Avant la régression
bootstrap, les sept premiers cas passaient (10,85 s) ; après fix, huit passent
(10,14 s). Vraies sessions signées, serveur HTTP local réel, données temporaires,
aucun contournement d'authentification. Couverture : anonyme 401, non vérifié 403,
identité du corps ignorée, opt-in par compte/idempotent, aucun seed d'activité,
refus avant client Garmin, données Alice/Bob séparées, conservation octet pour
octet du profil, des déclarations, de la mémoire et des messages lors du setup
tardif simulé, compte Garmin existant non perturbé et dix GET du bootstrap.

Le navigateur a ensuite reproduit un deuxième défaut latent, désormais accessible
dans ce parcours : l'éditeur d'un profil déjà complet recevait nextStep=complete,
converti en index -1 puis 0, et bouclait sur l'objectif sans ouvrir les préférences.
Fix A 5dc694c327991dc46e92898afde0876e0bf81eae, trois lignes UI : suivre la
prochaine étape canonique lorsqu'elle existe, sinon avancer dans l'éditeur,
éviter la redirection Garmin en découverte et recharger le profil à la fin.
Contrats API et formulaire existants conservés. 33 ciblés UI/onboarding/backend
réussis, puis huit API indépendants réussis. Aucun nouveau moteur ou onboarding.

B a rejoué le chemin réel après fix : objectif POST 200 → disponibilités →
subjectif/préférences → Terminer → dialogue fermé → dashboard rechargé.
La préférence est persistée, découverte toujours active, bandeau visible et
sync masquée. Console fraîche sans erreur ni avertissement sur ce chemin.
La suite complète unique est lancée sur le code combiné stable 20c85c0,
reports/phase5-full.log et reports/phase5-full.xml ; résultat final à compléter.
