# Coach et gestion locale des séances V2 — livraison candidate

25 octobre 2026. Base : `3d117e75ddc538805a8551a6a27113e8ff865562`.
Branche candidate : `codex/motionest-coach-v2-candidate`.
Code applicatif et tests Python de la suite finale : `a3d49d6c4413a455d6f907e24c3614702928d272`.
Les changements suivants concernent les sélecteurs du banc navigateur et ce rapport, sans modification applicative.

## 1. Cause initiale et correction

Le dashboard lisait les séances canoniques V2 détaillées ; le coach recevait seulement quatre champs de calendrier et utilisait le plan historique pour ses lectures exactes et ses actions. Les 35 minutes étaient déjà transmises, mais les consignes et blocs V2 étaient omis. Une ancienne action par indice n'était pas une mutation de la séance V2 affichée.

`coach_calendar_context.build_coach_calendar_context()` lit maintenant directement `get_plan_proposal_state()`, indépendamment de l'étape d'onboarding. La projection fournit les champs sportifs utiles, l'identité, le calendrier, l'acceptation et la livraison disponibles, avec provenance et disponibilité explicites. Le lecteur déterministe privilégie cette source V2 ; le plan historique reste distinct.

La projection supplémentaire est bornée à 16 000 caractères JSON, sept séances et huit blocs détaillés par séance. Les détails puis, si nécessaire, les séances omis sont signalés. Le texte de réponse reste inférieur à 3 000 caractères. Des listes blanches filtrent les champs et les textes sensibles ; aucun compte, cookie, ID distant ou token n'est ajouté à cette projection.

La régression du mardi 27 octobre 2026 à 18 h, 35 minutes, reconnaît objectif et intensité. Elle distingue séance prévue enregistrée, accord Motionest, création Garmin, programmation et activité effectuée. Une source indisponible n'est jamais transformée en absence d'entraînement.

## 2. Actions effectivement livrées et preuves

| Action | Périmètre livré | Preuve |
|---|---|---|
| Consulter la semaine ou une séance V2 | Objectif, durée, intensité, structure disponible, provenance et états distincts | Tests du lecteur ; appel IA réel de consultation |
| Réduire une séance simple identifiée de 35 à 25 min | Préparation seule puis confirmation locale ; phases 5 + 15 + 5 = 1 500 s | Tests métier, intention IA réelle et navigateur avec réponse contrôlée |
| Modifier une consigne d'intensité qualitative | Patch local validé et confirmé ; ne prétend pas mesurer une charge physiologique | Tests des patches et parcours navigateur |
| Déplacer une séance identifiée | Date/horaire cohérents, validation du plan, confirmation locale | Intention IA réelle et déplacement navigateur vers jeudi 29 octobre |
| Changer l'heure de la séance du soir | Identité exacte, nouvelle heure explicite, séance du matin inchangée | Tests du moteur et navigateur : 18 h → 20 h |
| Annuler un brouillon | Bouton Annuler ou demande explicite « Annule, finalement. » ; aucun changement du plan/accord/livraison | Tests HTTP, intention IA et navigateur |
| Modifier une séance déjà acceptée mais non livrée | Accord de la version modifiée devient obsolète ; sœur inchangée conservée | Tests des empreintes et des consentements |

Les appels IA réels démontrent la lecture et les intentions structurées. Les parcours navigateur emploient une réponse IA contrôlée avec le vrai handler, le vrai worker, les stores et l'interface. Il ne s'agit pas d'un unique parcours navigateur connecté en direct au fournisseur IA ; cette distinction est conservée dans le verdict.

## 3. Actions volontairement bloquées et limites de compatibilité

- **Retrait local V2 non activé.** Le contrat exige exactement la fréquence confirmée et au moins une séance. Aucun contournement du compteur, changement implicite de fréquence ou marqueur `removed` n'a été ajouté. Le contrat proposé et les tests sont dans [CONTRAT_RETRAIT_LOCAL_V2.md](CONTRAT_RETRAIT_LOCAL_V2.md).
- Séance avec ID Garmin, intention d'upload, programmation demandée ou livraison potentielle : modification bloquée, y compris après révision de la même identité. Aucun déblocage d'incertitude.
- Activité locale constatée à cette date : blocage conservateur, même si le rapprochement d'exécution est ambigu. Aucune suppression d'activité réalisée.
- Séance passée, refusée, décision de révision ou journal corrompu : refus explicite avant modification.
- Séance sans identité stable : consultation conservée, mutation refusée. **Certaines anciennes propositions V2 simples autorisent encore `session_id=None` ; elles ne deviennent pas modifiables automatiquement.** Aucun ID inventé, aucune migration ni correspondance par indice n'est utilisé. Leur évolution nécessite une décision séparée.
- La réduction automatique conserve échauffement et retour au calme pour un effort simple sans récupération. Répétitions ou blocs complexes ambigus : précision ou structure complète cohérente exigée. Pas de réduction arbitraire ni changement du seul libellé de durée.
- La validation exécutable du nouveau moteur est actuellement limitée aux séances de course convertibles par l'adaptateur existant. L'analyse multisport et la consultation des autres sports restent présentes.
- Aucun ajout unitaire, lot de deux patches distincts, remplacement distant ou annulation Garmin n'est livré.

## 4. Brouillons et confirmation

`CoachResponse.session_action_v2` contient une intention typée `modify`, `move` ou `change_time`, avec `session_id` exact. L'action historique reste `none`.

`coach_session_actions.prepare_session_action()` construit et valide le candidat complet via `preview_plan_session_revision()`. Il ne modifie ni le plan, ni les accords, ni la livraison. Seul un journal de brouillons séparé est écrit, dans l'espace du même utilisateur.

L'interface affiche « Actuellement » et « Après confirmation », avec les phases, puis **Confirmer** et **Annuler**. Les routes POST authentifiées sont :

- `/api/coach/session-action/confirm`, corps `{draftId}` ;
- `/api/coach/session-action/cancel`, corps `{draftId}`.

La confirmation relit source, identité, empreinte des entrées, du plan, de la séance et de la décision. Elle revalide le candidat puis réutilise `revise_plan_session()`. Le seul effet sportif est local et unitaire. La séance modifiée demande ensuite une nouvelle acceptation ; l'envoi Garmin reste séparé.

Une annulation ferme le brouillon, sans toucher les fichiers sportifs. Un brouillon annulé ne peut plus être appliqué. Annuler un brouillon déjà confirmé ne prétend pas revenir en arrière : le résultat reste « confirmé ».

L'ancienne route HTTP `/api/plan-proposal/session`, qui écrivait sans aperçu, renvoie désormais `409 confirmation_required`. Aucun client Web livré ne l'appelait selon la recherche de compatibilité. La fonction Python de révision est conservée et réutilisée.

## 5. Concurrence, obsolescence et erreurs partielles

Ordre des verrous du moteur : livraison → proposition → acceptation. La révision canonique participe au verrou livraison → proposition. Confirmation et annulation concurrentes sont sérialisées dans le même processus.

Deux confirmations d'un même brouillon sont idempotentes. Deux brouillons différents sur l'ancienne version donnent une seule application, puis un refus d'obsolescence. Permutation des séances, modification d'une sœur, nouvelle décision, activité importée ou intention d'upload apparue entre aperçu et confirmation invalident ou bloquent l'application.

Le statut durable `applying` précède l'écriture du plan. Si le plan a été écrit avant une interruption de journal, une nouvelle confirmation reconnaît l'empreinte après application sans appliquer deux fois. Une annulation d'un état en cours est refusée ; une erreur d'E/S n'est jamais présentée comme une annulation réussie. La route renvoie un message de vérification explicite sans chemin privé.

Limites : pas de transaction interprocessus, de garantie `fsync` ou de retour arrière général ; les verrous sont ceux du processus Web. Le journal est borné à trente brouillons et 2 Mo. S'il est plein, une nouvelle préparation est refusée plutôt que d'effacer silencieusement des décisions.

## 6. Anciens chemins conservés

Les vraies utilisations historiques sans calendrier V2 restent compatibles. Les anciennes actions conversationnelles sont bloquées pour un parcours V2 ; le modèle ne peut pas convertir une identité V2 en indice historique. Les anciennes réponses déjà enregistrées sont également refusées par le garde serveur.

Les parcours historiques possèdent encore les limites documentées lors du diagnostic : ciblage par indice et remplacement Garmin global possible après accord historique. Ils ne sont pas présentés comme le nouveau protocole V2. Aucune refonte de leur publication réelle n'a été entreprise.

`training_cycle.list_messages()` devient seulement un import différé pour éviter le cycle introduit par les dépendances du schéma conversationnel. Aucun calcul ni générateur sportif n'est modifié par ce changement d'import.

## 7. Résultats des tests

| Validation | Réussis | Échoués | Erreurs | Ignorés |
|---|---:|---:|---:|---:|
| Ciblés finaux : projection, actions, qualité indépendante, HTTP et ancienne régression 3C | 89 | 0 | 0 | 0 |
| Suite complète finale | **1 202** | **0** | **0** | **0** |

Durées pytest affichées : 13,51 s pour les ciblés, 247,66 s pour la suite complète. Quatre avertissements Pydantic existants dans `garminconnect`, sans nouvel avertissement applicatif identifié.

La première suite complète donnait 1 195 réussites et un échec : le lecteur déterministe interceptait une question de conseil sur une double journée débutant. Le déclencheur exige maintenant une intention explicite de consultation ; trois régressions ont été ajoutées. L'ancien banc 3C garde ses assertions de cinq appels factices et 750 tokens ; il passe sur le candidat intégré. Cet échec initial n'est pas effacé des logs.

Une réserve de transport du nouveau test HTTP anonyme a été reproduite : WinError 10053 avant réception du statut, avec en-têtes/corps envoyés séparément. La revue indépendante a conservé 29 réussites/1 échec, puis le même échec isolé. Le banc transmet maintenant en-têtes + corps JSON en un seul `sendall`, sans délai ni retry. L'assertion `401`, le corps réel et les contrôles d'absence d'effet sont conservés. **Aucune modification de l'authentification n'a été nécessaire.** Cela stabilise le test observé, sans promettre l'absence universelle d'incidents réseau Windows.

Pièces locales ignorées par Git : `reports/coach-v2-final-targeted.xml`, `reports/coach-v2-full-final.xml`, logs correspondants et résultats de la première suite.

## 8. IA réelle et consommation

Six appels, aucun retry, aucun appel additionnel ; données synthétiques exclusivement. Modèle réellement journalisé : `gpt-5.6-sol`.

| Scénario | Résultat relu |
|---|---|
| Semaine | Mardi 27 octobre, 18 h, 35 min, phases 5/25/5 et intensité correctement citées ; états inconnus explicités |
| Raccourcir mardi | Identité `evening`, intention `modify`, cible 25 min |
| Déplacer à jeudi | Identité `evening`, 29 octobre 2026, 18 h |
| Supprimer mardi | Retrait V2 non actif expliqué, aucune action de suppression |
| Celle du soir | Identité `evening`, réduction à 25 min, en réutilisant le contexte de clarification |
| Annule finalement | Aucune nouvelle intention ; annulation effective prouvée séparément via le worker et le navigateur |

**6/6 réussites techniques et fonctionnelles au niveau lecture/intention.** 39 283 tokens d'entrée + 996 de sortie = **40 279 tokens**. Le contexte du sportif ne contient aucun secret. Le journal d'usage est le seul ajout autorisé aux données temporaires de ces probes.

Voir [VALIDATION_COACH_SEANCES_IA.md](VALIDATION_COACH_SEANCES_IA.md) et `reports/coach-v2-real-ai.json`. La correction ultérieure du déclencheur de lecture ne change pas les instructions ou le schéma utilisés par les six probes, qui appellent explicitement le fournisseur sans court-circuit déterministe.

## 9. Navigateur et captures

Skill utilisé : Playwright CLI local. Interface et handler réels, sessions signées synthétiques, stores temporaires, fournisseur simulé ; aucun compte participant ni Garmin réel.

Huit contrôles métier ont abouti, avec vérification des fichiers ou séances concernés : annulation sans effet ; confirmation 35→25 et sœur inchangée ; aperçu obsolète refusé ; séance publiée bloquée ; changement d'heure du soir ; déplacement à jeudi ; consigne qualitative plus facile ; annulation conversationnelle sans effet. Le calendrier affiche ensuite 25 min pour la séance modifiée.

Les contrôles ont été réalisés en segments. Le banc navigateur a été corrigé pour réinitialiser réellement sa fixture au lieu de reprendre le cache de génération, puis pour cibler les éléments de texte exacts du DOM. Les timeouts initiaux du banc sont conservés ; aucune correction applicative n'a été déduite de ces erreurs de fixture/sélecteur. Les derniers cas ont été repris sans répéter les contrôles déjà réussis.

Captures dans `reports/coach-v2-browser/` :

- `01-preview.png` et `01-preview-full.png` : détails avant/après et boutons ; la conversation défile à l'intérieur du panneau ;
- `02-cancelled-full.png`, `03-confirmed-full.png`, `04-calendar-25-full.png` ;
- `05-stale-blocked.png`, `06-published-blocked.png` ;
- `07-evening-time.png`, `08-moved.png`, `09-easier.png`, `10-natural-cancel.png`.

Console du navigateur : zéro erreur, zéro avertissement observé. Les deux serveurs de fixture ont été arrêtés ; ports 27983 et 29268 sans listener. Le dernier journal serveur confirme zéro appel Garmin et l'arrêt du serveur. Les appels de modèle du navigateur sont factices et distincts des six appels IA réels.

## 10. Fichiers et branches

Application : `coach_calendar_context.py` et `coach_session_actions.py` nouveaux ; raccordements `coach_chat.py`, `dashboard.py`, `dashboard_v2.html` ; extraction de validation pure/verrou dans `plan_proposal.py` ; import différé dans `training_cycle.py`.

Tests : quatre nouveaux modules `test_coach_calendar_context.py`, `test_coach_session_actions.py`, `test_coach_session_actions_quality.py`, `test_coach_session_actions_web.py`.

Validation : `scripts/validate_coach_sessions_v2.py`, `validate_coach_sessions_browser.py`, `validate_coach_sessions_browser_steps.js`. Documentation : contrat de retrait, validation IA, présent rapport.

Branches agents conservées :

- `codex/motionest-coach-v2-reading`, livraison `9305d6f0ceb00cdac4623e1ba65e1195922a2724` ;
- `codex/motionest-coach-v2-actions`, livraison `2eb4662faac8b895c873c0668ec20a05bda8a833` ;
- `codex/motionest-coach-v2-quality`, livraison `5f8c80b260a5adb9ad737ce125048934223c643e`.

L'orchestrateur a repris uniquement les commits utiles sur sa branche candidate ; aucun merge dans `main`. Les quatre worktrees de cette mission et les anciens sont conservés, soit 46 worktrees référencés. La variante historique `04aece083b9e307fa38aba94feab7ea417e00877` reste accessible. Le checkout original avec ses modifications anciennes n'a pas été touché.

## 11. État de main et restrictions

`main` avant/après : **`3d117e75ddc538805a8551a6a27113e8ff865562`**, checkout propre.
Aucun push, déploiement, reset destructif, suppression de sauvegarde, modification mobile, nouveau connecteur, nouveau générateur, appel ou suppression Garmin réel.

## 12. Conditions avant intégration et travaux restant à décider

La candidate est prête pour revue dans le périmètre démontré : consultation V2 et mutations locales confirmées de séances de course identifiées et sûres. Elle n'est pas annoncée comme prise en charge universelle de toutes les anciennes propositions ou de la compatibilité Garmin réelle.

Avant intégration, valider explicitement : retrait maintenu inactif ; propositions sans identifiant en lecture seule ; blocage conservateur publié/incertain/refusé/activité ambiguë ; limite de réduction automatique simple ; fermeture de l'ancienne route HTTP d'écriture directe sans aperçu.

Les évolutions restantes sont des décisions séparées : contrat de retrait/fréquence/plan vide, attribution consentie d'identités aux anciennes propositions, remplacement ou réconciliation Garmin, mutations multisports exécutables supplémentaires, lot atomique de plusieurs séances et garantie interprocessus. Aucune de ces évolutions n'a été commencée.
