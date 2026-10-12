# Parcours produit Motionest

Ce document décrit les états visibles du produit. Les étapes serveur restent explicites afin que le parcours demeure reprenable et vérifiable.

## Transitions de la première configuration

| État serveur | Présentation précédente | Présentation produit | Décision enregistrée |
| --- | --- | --- | --- |
| `goal_assessment` | Évaluation de l’objectif | Faisabilité de l’objectif | Résultat GoalAssessment |
| `planning_recommendation` | Avis puis rythme séparé | Fréquence proposée, état de préparation | PlanningRecommendation |
| `confirm_frequency` | Confirmation de fréquence séparée | Fréquence proposée, accepter ou discuter | PlanningDecision |
| `frequency_unavailable` | Recommandation suspendue | Fréquence proposée, état de clarification | Aucune fréquence inventée |
| `ready_for_plan` | Contexte prêt | Ta première semaine, avant préparation | Fréquence confirmée |
| `review_plan` | Revue du plan généré | Ta première semaine, résultat et ajustement | PlanAcceptance |
| `dashboard` | Une longue page | Accueil, Ma semaine, Activités, Coach | Première semaine acceptée |

Le dashboard reste inaccessible avant l’acceptation de la première semaine. Une préparation échouée ou interrompue n’est jamais présentée comme acceptée.

## Navigation du dashboard

- `#home` : prochaine séance acceptée et objectif compact.
- `#week` : représentation unique de la semaine.
- `#week/session/<index>` : semaine avec la séance choisie ouverte.
- `#activities` : historique réel importé et détail de l’activité choisie.
- `#activities/<activity-id>` : sélection directe d’une activité.
- `#coach` : conversation existante, attente, erreurs et nouvelles tentatives.

Les vues inactives utilisent `hidden` et `inert`. L’historique du navigateur, le rechargement et les liens directs restaurent la vue choisie.

## Limite connue

Une demande d’ajustement est enregistrée et reste visible, mais le backend actuel ne génère pas encore un nouveau PlanProposal à partir de cette demande. L’interface indique donc honnêtement que la demande est enregistrée et conserve le plan existant. Elle ne simule pas un plan révisé.
