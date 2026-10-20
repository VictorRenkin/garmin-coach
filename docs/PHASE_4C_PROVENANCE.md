# Phase 4C — Provenance et complétude du contexte conversationnel

Base : `4de172b0ac2cfd082ef4b3db04122329071d201c`, branche isolée `codex/motionest-provenance-4c`. L'examen reste limité à l'écart confirmé de présentation; aucun moteur d'historique ou de confiance n'est reconstruit.

## Défaut confirmé

`conversation_sport_context.build_conversation_sport_context` indique `activity_list_complete=True` lorsque toutes les activités locales sélectionnées sont énumérées avant troncature. Cet indicateur ne signifie pas que l'import amont est terminé. Or le contexte normal du coach ne transmettait ni statut ni fin de pagination d'import, contrairement à la recherche historique 1.2B qui expose déjà `import_status` et `coverage_status`. Avec un import interrompu, une liste locale complète pouvait donc être présentée comme un historique global complet.

La consigne préexistante « utilise summary pour les totaux complets » est également ambiguë : ces synthèses totalisent les données locales sélectionnées, pas l'entraînement réel inconnu. `DataConfidence`, les bornes de portée et les indicateurs de métriques manquantes existaient déjà; ils ne sont pas recalculés.

Reproduction avant correction : cinq assertions échouent sur l'absence du champ `import_status`, une assertion de conservation des métriques passe. Un cas de fixture initialement conçu avec `status=error` a été précisé ensuite : le lecteur existant refuse cet état et le dashboard expose `status=unavailable`, sans histoire vide inventée. Ce comportement est conservé, pas corrigé artificiellement.

## Correction minimale de projection

Dans `conversation_sport_context.py`, six lignes ajoutent :

- lecture du lecteur d'état existant `load_history_import(detect_stale=False)` dans le contexte utilisateur; pas de mutation de heartbeat ou de stockage;
- projection explicite de `import_status` et `import_pagination_complete`, uniquement pour la source multisport; null pour une provenance legacy sans état d'import applicable;
- commentaire précisant le sens local de `activity_list_complete`, conservé pour compatibilité et troncature.

Ces deux champs sont inclus dans le budget existant de 48 000 octets. Source, scope, date de référence et cutoff restent ceux des structures canoniques déjà transmises; aucun identifiant, erreur brute, jeton, ancre de pagination ou donnée personnelle n'est recopié. Les synthèses, distances, durées, DataConfidence et présentation numérique 4A sont inchangés.

## Tests ciblés de ce checkpoint

| Exécution | Réussis | Échecs | Ignorés |
|---|---:|---:|---:|
| Projection provenance + contexte multisport + numériques 4A, avant ajout de deux cas de portée/isolation | 40 | 0 | 0 |
| Huit cas provenance finaux + recherche 1.2B + DataConfidence | 49 | 0 | 0 |

Les huit cas du nouveau `tests/test_coach_provenance_4c.py` contrôlent import running/interrupted/complete indépendamment de la liste locale, import invalide indisponible, métriques et libellés numériques inchangés, observation locale vide, portée de trois mois conservée malgré import terminé et isolation des états de deux comptes. Les régressions de budget de projection existantes passent dans la première exécution. Quatre avertissements Pydantic préexistants subsistent.

Aucun appel IA ou Garmin ni suite complète n'est effectué par cet agent. Les preuves XML sont dans `reports/phase-4c-before.xml`, `phase-4c-targeted.xml` et `phase-4c-confidence-search.xml`, ignorées par Git.

## Consignes conversationnelles à intégrer séquentiellement

L'agent 4D est propriétaire de `coach_chat.py` pendant son travail. Ce checkpoint ne modifie donc pas ce fichier simultanément. Après stabilisation 4D, préciser séquentiellement que :

1. `activity_list_complete` décrit seulement la liste locale affichée; `summary` conserve les totaux locaux observés même lorsque cette liste est tronquée.
2. `import_status` et `import_pagination_complete` décrivent l'avancement amont; un import terminé reste borné à `history.scope`, cutoff et référence, sans preuve d'enregistrements exhaustifs de la vie sportive.
3. Une période sans activité locale n'est pas une preuve de repos ou d'absence réelle d'entraînement; null n'est ni zéro ni une estimation.
4. La recherche ciblée conserve ses propres `coverage_status`, période et source; ne pas remplacer une période demandée par les fenêtres 28/84 jours.

Cette étape de consignes et la validation combinée sont coordonnées par l'orchestrateur; le présent checkpoint de projection ne les annonce pas déjà réalisés. Aucun commit dans `main`, push, changement d'import ou de Garmin. Les sauvegardes et worktrees existants sont préservés.
