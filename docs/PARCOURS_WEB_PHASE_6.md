# Phase 6 — Parcours Web et raccordement HTTP

Base réelle : `ee41c9e822c79060afa837f7f66c6db08e69dbf9`. Branche isolée `codex/motionest-web-journey-6`. Les composants des phases précédentes sont réutilisés; aucun nouveau moteur d'onboarding, d'historique, de confiance ou de séances. Aucun fichier de l'application Expo n'est ouvert ou testé par cet agent.

## Méthode et couverture des vingt étapes

Les tests ajoutés dans `tests/test_web_journey_phase6.py` empruntent les vraies routes `DashboardHandler`, un serveur local, des comptes temporaires et des sessions signées. Ils complètent les tests existants aux raccordements manquants. Les transports e-mail/anti-robot/IA/Garmin sont explicitement simulés : aucune validation de ces services réels n'est revendiquée. La proposition est produite par un faux fournisseur avec une entrée synthétique, mais validation, stockage, empreintes, décisions et livraison locale ne sont pas court-circuités dans la chaîne HTTP.

| Étape | Preuve réutilisée ou ajoutée |
|---|---|
| 1. Inscription | Nouveau parcours HTTP `test_registration_verification_login_onboarding_discovery_and_logout_use_real_sessions`; création réelle du compte, transport e-mail/anti-robot simulé |
| 2. Vérification | Même test : lien courant envoyé, GET `/verify-email`, redirection positive `/?verified=1`; login refusé avant validation |
| 3. Connexion/déconnexion | Même test : cookie réellement délivré après login, logout avec expiration et API sans cookie refusée; `test_multitenant_security.py` conserve les autres contrôles |
| 4. Objectif | POST réel `/api/onboarding/goal`; modèles et validations V2 existants |
| 5. Disponibilités/préférences | Deux POST V2 réels, sans réimplémentation du formulaire ou des contrats |
| 6. Profil/déclarations | Tests de reprise V2 existants et instantanés octet pour octet lors de la connexion tardive |
| 7. Sans Garmin | Opt-in HTTP puis dashboard 200 dans la chaîne d'inscription et dans la régression tardive |
| 8. Dashboard vide | Parcours HTTP de découverte et dix routes de lecture déjà vérifiées par `test_discovery_phase5_quality.py`; états UI vides relèvent de B |
| 9. Coach sans historique | POST/GET de messages avec worker de réponse simulé; le worker lit le vrai contexte et vérifie zéro activité locale, sans appel IA |
| 10. Connexion tardive simulée | Nouveau test réel POST `/api/garmin/connect`, transport/auth et persistance de tokens fictifs uniquement; setup explicite simulé |
| 11. Profil/conversation conservés | Comparaison des quatre fichiers profil, entrées canoniques, mémoire et messages avant/après connexion tardive |
| 12. Historique multisport | Le faux setup persiste course/vélo/natation avec le stockage existant; le vrai contexte canonique restitue ces trois sports |
| 13. Questions/comparaisons | Tests existants de recherche 1.2B, numériques 4A, provenance 4C et lisibilité 4D relancés; aucune promesse de validation sémantique d'un vrai modèle par le worker simulé |
| 14. Proposition | POST `/api/plan-proposal/generate`, validation et JSON stocké réels, fournisseur synthétique |
| 15. Plusieurs blocs | Chaîne HTTP générant deux séances à deux blocs chacune; contrats et adaptateurs existants, régressions précises de secondes déjà présentes dans les tests 3B |
| 16. Doubles journées | Même date 08 h/16 h, identifiants distincts et contraintes déclarées synthétiques; validateur 3C réel |
| 17. Accord/refus unitaire | POSTs de décisions matin accepté/soir refusé, puis accord ultérieur du soir; le POST de livraison est refusé avant l'accord de chaque séance |
| 18. Livraison simulée | `VerifiedClient`, deux IDs et programmations distincts; relance idempotente; nouveaux cas de réponse perdue et préflight local ci-dessous |
| 19. Retour dashboard | GET `/dashboard` répond 200 pour le compte connecté synthétique de la chaîne de proposition |
| 20. Isolation | Bob conserve zéro activité/message/proposition d'Alice; tests de session, manager et espaces utilisateurs existants relancés |

Les contrôles graphiques, modales, viewports et messages de B sont complémentaires, pas remplacés par ces preuves HTTP. L'application Expo reste hors périmètre.

## Défaut confirmé avant correction : connexion tardive refusée

En découverte explicite, le GET `/connect-garmin` répondait 200 mais le POST du formulaire refusait 409 : le garde de route autorisait seulement `connect_garmin` et `import_failed`, tandis que le choix de découverte plaçait normalement l'utilisateur à `dashboard`. Le test ajouté échoue sur cet arbre avant correction (`reports/phase6-web-before.xml`).

La correction dans `dashboard.py` autorise ce POST depuis `dashboard` uniquement si le drapeau explicite est vrai et qu'aucun token n'est présent. Elle conserve le parcours normal et ne permet ni compte non vérifié, ni compte déjà connecté, ni onboarding non terminé sans choix de découverte. Trois cas négatifs ciblent ces limites. Le parcours positif attend le contrat existant HTTP 202, pas une réponse 200 inventée. Aucun changement dans le code de connexion Garmin lui-même.

## Préflight canonique de l'intention Garmin incertaine

La livraison C `6c27f9d` est fusionnée réellement après le checkpoint A `2e8b1ef`. C reste propriétaire de `garmin_delivery.py`; A ne le modifie pas. Le POST de livraison appelle son helper local `assert_upload_retry_safe` après les gardes d'identité/accord existantes et **avant** `connect(load_settings())`; le publisher conserve sa seconde vérification sous verrou et ses autres contrôles.

Deux nouveaux cas HTTP simulent création distante avec ID manquant ou timeout. Après le premier appel explicite : une connexion, un upload fictif, zéro programmation. La relance renvoie 409 `upload_uncertain` sans seconde connexion/upload/programmation. Le GET indique une création incertaine sans ID inventé ni message privé brut. Ce blocage est une protection de prudence, pas une recherche ou confirmation d'un workout Garmin réel.

Le seul test existant de présentation de route qui remplaçait déjà `connect` et le publisher est adapté pour remplacer aussi ce nouveau boundary local; il vérifie désormais l'ordre `preflight → connect → publish` tout en conservant ses assertions d'IDs et de statuts. Les nouveaux tests HTTP de garde utilisent le vrai helper. Cette adaptation de fixture est distincte du défaut métier tardif; aucun test de sécurité n'est neutralisé.

## Validations de l'agent A

- Huit cas HTTP ajoutés; première preuve du refus tardif conservée avant correction.
- **62 réussis, 0 échec, 0 ignoré en 21,10 s** : nouveaux parcours, module C d'upload incertain, livraison et qualité calendrier. XML `reports/phase6-web-canonical-preflight.xml`.
- **104 réussis, 0 échec, 0 ignoré en 20,85 s** : sécurité multicomptes, onboarding V2, recherche historique et contrôles 4A/4C/4D. XML `reports/phase6-web-auth-context.xml`.
- Quatre avertissements SDK Pydantic préexistants; aucun nouvel avertissement applicatif attribué ici.

Une fixture d'inscription utilise le dernier lien reçu : le login non vérifié régénère volontairement un lien et invalide le précédent. Les essais préparatoires ont précisé ce contrat ainsi que le statut existant 202 de connexion; aucun changement d'authentification ou d'assertion métier pour masquer un défaut. Les XML d'essais précédents sont conservés.

Checkpoint applicatif A+C : `05096e7268b7658d9261ab99f79dba955bc91c49`. La suite complète finale appartient à l'orchestrateur sur la branche consolidée après B; aucun résultat complet n'est anticipé. Aucun appel IA payant ou Garmin réel, aucune modification de `main`, aucun push ni suppression de sauvegarde/worktree.
