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
