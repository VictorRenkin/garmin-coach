# Contrat proposé — retrait local d’une séance V2

## Décision actuelle : spécification uniquement

Base inspectée : `3d117e75ddc538805a8551a6a27113e8ff865562`. Cette livraison n’active aucun retrait et n’ajoute aucun schéma applicatif. Elle réutilise le diagnostic du 25 octobre 2026 (`.codex/reports/motionest-coach-seances-20261025-bw5665qx/RAPPORT.md`, hors dépôt) et les contrats existants.

Un retrait physique ciblé n’est pas représentable en conservant les validations actuelles :

- `PlanProposalV2.sessions` exige au moins une et au plus sept séances (`plan_proposal.py`, champ `sessions`). Retirer la dernière ne produit pas une proposition vide valide.
- `validate_plan_proposal` exige exactement `plan_constraints.confirmed_sessions_per_week` séances, code `session_count_mismatch`. Retirer une séance d’une proposition validée ferait diverger le calendrier et la fréquence acceptée.
- `revise_plan_session` met à jour exactement un élément identifié, conserve son `session_id`, reconstruit et revalide toute la proposition ; il n’existe ni opération de retrait, ni tombstone, ni compteur de séances actives distinct.

Il serait incorrect de diminuer automatiquement la fréquence acceptée, d’insérer une séance fantôme ou de conserver un objet marqué « retiré » dans un champ inconnu en contournant la validation. Ces solutions changeraient le contrat sans autorisation explicite de sa sémantique.

## Quatre opérations distinctes

| Demande | Contrat actuel / effet admissible |
|---|---|
| Refuser une séance ou une proposition | Journal d’acceptation existant. La séance reste consultable dans la proposition. Un refus ne constitue pas un retrait. |
| Retirer une séance locale du plan | Nouveau contrat nécessaire ; aucune route ou action active dans cette mission. |
| Supprimer ou remplacer une séance distante Garmin | Hors de ce contrat ; aucun appel, adoption d’ID, retrait d’intention ou déblocage manuel. |
| Supprimer une activité effectuée | Hors périmètre ; aucune activité ni donnée historique ne doit être effacée. |

L’interface actuelle doit expliquer cette limite et peut proposer de préciser ou de réviser la planification selon les capacités déjà livrées. Elle ne doit pas annoncer « séance supprimée » après un simple refus, ni publier silencieusement une nouvelle semaine.

## Contrat futur à décider avant activation

1. Définir séparément la fréquence hebdomadaire acceptée et le nombre de séances effectivement retenues : soit une exception explicite et versionnée à la fréquence, soit une nouvelle décision de planification avec consentement distinct. Le choix n’est pas fait automatiquement ici.
2. Définir le cas zéro séance et son rendu, sans fabriquer un plan minimal artificiel. Ne pas simplement abaisser `min_length` sans traiter les consommateurs.
3. Conserver une identité stable et une preuve du retrait dans un journal séparé ou une version complète approuvée. Ne pas réutiliser l’identité pour une autre séance, ne pas supprimer les preuves d’accord/refus ou de livraison.
4. Préparer sans effet un aperçu ciblant compte, période, empreintes d’entrées/proposition/séance et identité. Date et horaire servent à expliquer, jamais à remplacer l’identité ; deux séances le même jour imposent une clarification si la cible est ambiguë.
5. Montrer « Confirmer le retrait local » et « Annuler ». Annulation : aucune écriture de plan, accord, activité ou livraison. Persistance éventuelle d’un statut d’annulation du brouillon à distinguer explicitement d’une mutation du plan.
6. À la confirmation, revérifier compte, identité, empreintes, activité déjà effectuée et statut distant dans une section critique commune avec les écritures concurrentes. Refuser si obsolète, publié ou incertain, sans effet partiel ; un blocage conservateur n’est pas une résolution distante.
7. Construire et valider le candidat complet selon le nouveau contrat décidé. Préserver toutes les séances sœurs, leurs identités, structures, accords et livraisons ; ne pas retirer de séance supplémentaire pour équilibrer un compteur.
8. Définir atomicité et idempotence entre proposition, journal de retrait et consentements, y compris crash et confirmations concurrentes. Une seule mutation effective ; ne pas confondre verrou dans un processus et transaction durable globale.
9. Aucun effet Garmin dans le retrait local. Une éventuelle suppression distante demanderait une autre décision, des droits et un consentement propres ; l’accord local ne vaut pas consentement Garmin.

Ce sont des critères de conception, pas des structures ou fonctions ajoutées au produit. La qualification d’un tel contrat devra précéder ses routes et boutons actifs.

## Régressions du contrat actuel

`tests/test_coach_session_actions_quality.py` vérifie le refus d’une proposition à deux séances ramenée à une sans changer la fréquence, l’absence de contrat vide, et le rejet d’un pseudo-champ `removed`. Ces tests ne prouvent pas qu’un retrait fonctionne ; ils démontrent pourquoi il reste bloqué sans modification de contrat. Les données sont synthétiques et les chemins redirigés vers un répertoire temporaire.

La suite indépendante des modifications/déplacements sera ajoutée après disponibilité de l’API candidate stabilisée. Aucun développement mobile, appel IA ou Garmin réel, lecture de configuration personnelle, commit sur main, intégration automatique ou push.

## Qualité indépendante du parcours d’actions livré

Checkpoint applicatif B contrôlé : `018e54d57dccaa9e5db66e2203ae5d5c94254021`. API `coach_session_actions` : intentions `modify`, `move`, `change_time`, identité obligatoire, préparation puis confirmation/annulation explicites. Aucune intention de retrait ajoutée.

Le fichier qualité comporte désormais 19 cas, dont les trois constats de contrat ci-dessus. Les 16 autres couvrent :

- Permutation du plan après aperçu : confirmation refusée, aucune autre cible choisie par indice.
- Modification de la séance du soir : matin et fichier legacy sentinelle inchangés. Cette sentinelle démontre l’absence d’écriture dans l’ancien plan, pas une nouvelle campagne de compatibilité du moteur legacy.
- Accord ou refus arrivé après préparation : aucune confirmation silencieuse par-dessus cette nouvelle décision.
- Deux brouillons différents confirmés simultanément : une application, l’autre obsolète ; aucune modification de la sœur.
- Ancien record distant sans identité, ID confirmé ou intention incertaine : blocage conservateur sans effacer le record.
- Annulation d’une séance acceptée : plan, journal d’accords et livraison inchangés, compte voisin intact ; confirmation ultérieure du brouillon annulé sans effet. Le statut d’annulation est bien écrit dans le journal des brouillons : ne pas prétendre « aucun fichier écrit ».
- Candidat à blocs : demande de 25 minutes avec structure de 18 minutes refusée avant écriture ; candidat complet de 18 minutes accepté puis converti en exécutable de 1 080 secondes.
- Cible par indice, identité inexistante ou booléen à la place d’une durée : refus sans mutation.
- ID de brouillon d’un autre compte : lecture, confirmation et annulation refusées dans ce compte.
- Activité apparaissant après l’aperçu : confirmation bloquée, aucune suppression d’activité.

Les fixtures utilisent seulement des données synthétiques sous les chemins temporaires, avec `ROOT_DIR` également redirigé avant chargement éventuel de configuration ; entrée de génération et fournisseur de proposition factices. Les fonctions de validation, journaux, empreintes et révision sont réelles. Aucun appel IA ou Garmin, aucune route de publication remplacée globalement. Les tests de livraison, HTTP et compatibilité existants restent complémentaires ; cette première campagne ne revendique pas une validation navigateur de bout en bout.

Résultats : trois premiers cas contrat **3 réussis en 3,88 s** ; sur le premier checkpoint actions, **15 réussis en 3,40 s** ; sur le module B stabilisé, campagne qualité + développement :

```text
pytest tests/test_coach_session_actions_quality.py tests/test_coach_session_actions.py -q --disable-warnings
33 passed, 4 warnings in 5.75s
```

Soit **19 qualité + 14 développement**, 0 échec, 0 ignoré ; quatre avertissements Pydantic du SDK existants. Aucun défaut inédit reproduit dans cette campagne. L’intégration candidate des lectures, routes et garde des anciennes actions sera vérifiée séparément après son assemblage ; ne pas déduire de ces passes une fonction de retrait active ou une garantie de transaction inter-processus.

## Revue indépendante de la candidate assemblée

Candidate root `03b6de4` intégrée dans la branche qualité pour revue et tests ; delta `2211d82` ensuite relu sans nouvelle campagne large. Les changements applicatifs du delta traitent les erreurs typées de source et journal Garmin en erreurs publiques conservatrices, sans écriture ; l’API de retrait reste absente.

Lecture ciblée :

- L’ancienne route POST `/api/plan-proposal/session` renvoie `409 confirmation_required`. Recherche dans les clients Web/tests/scripts existants : aucun client livré ne l’appelait. `revise_plan_session` reste le backend réutilisé par la confirmation, pas une nouvelle voie d’application UI.
- Confirmation et annulation sont dans `do_POST`, après authentification ; aucun GET de consultation n’applique un brouillon.
- `store_response` prépare l’aperçu avant de persister sa réponse et ne modifie pas les trois stores métier. Une panne d’écriture du message après création du brouillon pourrait laisser un brouillon orphelin borné ; elle ne l’applique pas automatiquement. Ce n’est pas une transaction multi-fichiers garantie.
- Les anciennes mutations par indice sont refusées pour onboarding V2 ou proposition V2 disponible. Le parcours historique sans V2 reste distinct ; cette revue ne prétend pas refondre ses garanties distantes.
- `list_messages` libère le verrou du chat avant de relire l’autorité des brouillons. L’annulation naturelle relit ces états, au lieu de considérer comme pending un aperçu déjà annulé ou confirmé.
- Ordre observé des verrous des actions : livraison → proposition → acceptation ; révision : livraison → proposition. Aucun cycle inverse n’est démontré dans les chemins inspectés. Le wrapper différé `training_cycle.list_messages` résout la dépendance d’import sans changement de générateur. Il ne constitue pas une preuve universelle d’absence d’interblocage ou de concurrence inter-processus.

Campagne assemblée :

```text
pytest tests/test_coach_session_actions_quality.py tests/test_coach_session_actions_web.py -q --disable-warnings
29 passed, 1 failed, 4 warnings in 11.01s
```

L’échec concerne `test_anonymous_and_unconfirmed_direct_route_have_no_effect`, lors du POST anonyme JSON sur confirmation : `HTTPConnection.getresponse/read_status` lève `ConnectionAbortedError / WinError 10053` avant l’assertion 401. Une seule reproduction isolée autorisée : **1 échec, 0 réussite, 4 avertissements en 2,87 s**, même étape de transport. Aucun statut d’autorisation incorrect ni mutation n’est démontré par ces échecs. Le refus précoce d’une requête dont le corps n’est pas consommé est un contexte pertinent pour le reset Windows ; la cause système exacte n’est pas prouvée.

Aucune assertion, authentification ou fixture root n’a été modifiée par C pour cacher cet incident. Aucune boucle de relance. Un contrôle sans corps, s’il est retenu par l’orchestrateur, doit être distingué de ces échecs de transport et conserver la preuve initiale. La validation finale root, les scénarios IA et le vrai parcours navigateur restent rapportés séparément ; les 33 passes core précédentes ne sont pas une suite assemblée entièrement verte.

## Compatibilité : propositions V2 sans identité de séance

`PlanSessionV2.session_id` est optionnel. Les doubles journées imposent leurs identités par validation, mais une proposition simple dont toutes les séances ont `session_id=None` reste valide. La présence d’un calendrier V2 ne garantit donc pas que chaque séance soit modifiable par le nouveau parcours.

Deux cas supplémentaires vérifient cette limite sans changement de contrat :

- Une proposition réelle stockée de deux séances simples, mardi 35 minutes et samedi 30 minutes, sans IDs : projection disponible, consultation déterministe correcte, aucun repli sur un ancien plan contradictoire et aucun changement des stores.
- La même proposition : préparation de mutation sans identité ou avec une identité inventée refusée, aucun plan/accord/livraison modifié, aucun journal de brouillon créé. Aucun appariement par indice ou identité synthétisée depuis date/contenu.

Validation : **21 tests qualité réussis, 0 échec, 0 ignoré, quatre avertissements SDK en 4,21 s**, dont ces deux cas. Cette exécution n’inclut pas les tests HTTP intermittents précédents, dont les échecs restent conservés ci-dessus.

Minimum compatible livré : conserver la lecture des séances sans ID et refuser leur mutation ciblée, avec une explication de la nécessité d’une identité stable. Ne pas promettre une prise en charge universelle de toutes les anciennes propositions V2. Aucune migration, réécriture silencieuse, adoption d’ID ou repli sur le moteur legacy. Le produit ne reçoit aucun mapping automatique dans cette livraison.

Évolution minimale à décider séparément si l’on souhaite lever cette limite : garantir explicitement des identités stables aux futures propositions, puis définir un parcours consenti de remplacement/recréation d’une ancienne proposition, avec préservation des accords et des effets distants. Le contrat/prompt actuel n’est pas modifié ici et une simple régénération ne garantit pas à elle seule la présence d’IDs. Un tel choix ne doit pas convertir une ancienne séance publiée ou incertaine en nouvelle séance librement modifiable.
