# Motionest — phase 1.2A

Validation du 14 octobre 2026. Données exclusivement synthétiques ; appels via la configuration IA existante. Aucun import Garmin, aucune publication, aucun commit/push.

## Avant / après

Avant : cinq courses legacy, dix activités multisports au maximum sur 28 jours, snapshots historiques compacts. Après : activités sur deux fenêtres temporelles contiguës, résultats canoniques 28/84 jours et tendances chiffrées. Les vues legacy restent disponibles dans le contexte applicatif, mais sont retirées du payload modèle quand la projection canonique est disponible.

Le runtime transmet une projection des résultats déjà calculés. Aucun changement des formules historiques, des fenêtres 28/84, de l’import, de l’authentification ou des générateurs.

## Fichiers de cette phase

- garmin_coach/runtime_context.py : projection dédiée des résultats canoniques.
- garmin_coach/conversation_sport_context.py : deux semaines, sélection par dates, champs autorisés et budget explicite.
- garmin_coach/coach_chat.py : priorité canonique, consignes et suppression des doublons du payload IA.
- tests/test_conversation_sport_context.py : adaptation et extension des tests simulés.
- scripts/validate_conversation_12a.py : validation reproductible isolée ; --real effectue six appels.
- docs/PHASE_1_2A_CONTEXTE_COACH.md : présent rapport.

## Périodes

Pour la référence 2026-10-14 : dernière semaine du 8 au 14 octobre ; semaine précédente du 1 au 7 octobre ; 28 jours du 17 septembre au 14 octobre ; 84 jours du 23 juillet au 14 octobre. Bornes incluses. La référence reste celle choisie par le runtime/import. Les activités hors portée importée ne sont pas ajoutées.

| Profil | Derniers 7 jours | 7 précédents | 28 jours | 84 jours |
|---|---:|---:|---:|---:|
| beginner | 2 | 2 | 4 | 4 |
| busy | 21 | 21 | 42 | 42 |
| triathlon | 6 | 6 | 12 | 12 |
| old | 0 | 0 | 0 | 0 |
| no_hr | 1 | 1 | 2 | 2 |
| empty | 0 | 0 | 0 | 0 |

Le profil ancien contient une activité à J−100 : elle reste comptée dans la couverture globale, mais est exclue des périodes récentes. Le scénario sans FC conserve les valeurs manquantes à null. Les distances des profils synthétiques ne constituent pas des recommandations sportives.

## Métriques et provenance

Par fenêtre 28/84 : nombre d’activités, jours actifs, durée observée totale, distances pertinentes par sport, fréquence hebdomadaire, dénivelé, nombres par discipline, charge Garmin totale/moyenne et couverture des métriques. Tendances : valeurs courante/précédente, écarts absolus/relatifs et statut calculés par le moteur existant. Confiance : niveaux, statuts, raisons, données manquantes/obsolètes, avertissements et limites principales. Couverture globale et source/portée conservées séparément des fenêtres transmises.

Par activité : date, sport, durée, distance pertinente, FC moyenne/maximale, puissance moyenne, charge et Training Effect, provenance et métriques manquantes. Liste blanche : aucun titre privé, identifiant Garmin, identité ou GPS.

## Protection de taille

Budget : 48 000 octets UTF-8 JSON pour le bloc multisport (pas pour tout le prompt). Pas de plafond en nombre d’activités. Si dépassement, retrait des activités individuelles en équilibrant les deux semaines ; activités omises comptées, liste explicitement partielle. Les synthèses canoniques de semaine sont calculées sur toutes les activités avant réduction. Aucun scénario courant tronqué. Test avec budget réduit : troncature visible, totaux identiques et comptage exact des omissions. Si les seuls résumés dépassent le budget, erreur explicite et statut unavailable au lieu de suppression silencieuse des faits.

## Consommation IA mesurée

Comparaison finale : mode balanced, même demande et même fournisseur ; ancien format de payload/instructions reconstitué. Usage réelle rapportée par le fournisseur, sans estimation octets→tokens. Modèle routé existant : gpt-5.6-luna.

| Profil | Entrée avant | Entrée après | Sortie avant | Sortie après |
|---|---:|---:|---:|---:|
| beginner | 4440 | 6661 | 277 | 268 |
| busy | 4758 | 10845 | 272 | 288 |
| triathlon | 4777 | 7694 | 306 | 282 |

12 appels tentés au total : 11 réponses réussies, 1 échec initial sur le profil chargé en mode essential. 71,341 tokens enregistrés sur les réponses réussies ; consommation de l’appel échoué non mesurée, ce chiffre ne représente donc pas un total de facturation exhaustif.

Premier essai : une erreur de conversion de durée dans la réponse débutant et un échec du profil chargé. Ajustements : consigne de citer les totaux canoniques et de convertir les secondes correctement ; réduction des preuves de confiance dupliquées ; validation finale avec budget balanced. Aucun budget global de réponse du produit modifié.

## Appels finaux et lecture qualitative

### beginner

Sur les deux dernières semaines enregistrées, la fréquence est identique :
• Du 1er au 7 octobre : 2 séances, toutes en course à pied, pour 1 h et 10 km.
• Du 8 au 14 octobre : 2 séances, toutes en course à pied, pour 1 h et 10 km.

La charge enregistrée est également stable : 80 par semaine, avec 50 m de dénivelé hebdomadaire. Les séances observées ont chacune duré 30 minutes et couvert 5 km.

Cela suggère une régularité stable, sans hausse ni baisse mesurable entre ces deux semaines. Les tendances historiques sur 28 jours ne permettent pas de conclure à une progression de performance : les données de performance et les zones cardiaques sont indisponibles. La récupération physiologique n’est pas documentée non plus. Les activités listées sont complètes pour ces deux semaines.

### busy

Sur les deux dernières semaines enregistrées :

- Du 1er au 7 octobre : 21 séances, soit 21 par semaine, toutes en course à pied. 105 km et 10 h 30 enregistrés.
- Du 8 au 14 octobre : 21 séances, soit 21 par semaine, toutes en course à pied. 105 km et 10 h 30 enregistrés.

La comparaison factuelle montre donc une stabilité parfaite : même nombre de séances, même sport, même distance et même durée. La charge d’entraînement observée est également identique (840 par semaine).

Pour les tendances, les données indiquent une continuité, pas une progression ou une baisse. Les tendances sur 28 jours sont indisponibles car la fenêtre précédente est vide. La confiance historique est par ailleurs faible, même si la classification du sport et les métriques enregistrées sont disponibles. Les données de récupération et de performance ne sont pas disponibles, donc je ne peux pas conclure sur l’effet physiologique de cette charge.

### triathlon

Sur les deux dernières semaines enregistrées, le volume est identique :
• 1–7 octobre : 6 séances, sur 2 jours actifs — 2 de cyclisme, 2 de course, 2 de natation.
• 8–14 octobre : 6 séances, sur 2 jours actifs — 2 de cyclisme, 2 de course, 2 de natation.

Chaque semaine totalise 3 heures enregistrées, soit 30 minutes par séance, avec 10 km indiqués dans chacun des trois sports. La charge d’entraînement observée est de 240 par semaine. Les données montrent donc une stabilité parfaite entre ces deux semaines, pas une hausse ni une baisse. Les tendances sur 28 jours ne permettent pas de comparer à une période antérieure : la fenêtre précédente est vide. La confiance historique globale est faible, car la couverture commence le 4 octobre, même si la classification des sports est élevée. Les données de récupération et les zones cardiaques ne sont pas disponibles.

Ces trois réponses finales identifient les bonnes quantités et disciplines, convertissent correctement les durées et distinguent comparaison hebdomadaire et tendance historique indisponible. Cela valide ces exemples, pas toute réponse future du modèle.

## Tests

- 152 tests : contexte conversationnel, runtime, intégration Motionest, mémoire, historique, état et confiance.
- 119 tests supplémentaires : chat, évaluation objectif et décisions de planification.
- Après les derniers ajustements : 89 tests runtime/chat/mémoire/contexte repassés.
- 271 tests distincts au total ; aucune erreur. Quatre avertissements Pydantic préexistants provenant de garminconnect.
- Isolation utilisateurs, champs sensibles, deux semaines denses, triathlon, historique ancien/vide, absence FC, limites de dates, égalité des valeurs canoniques et troncature testés.
- git diff --check : aucune erreur de whitespace.

## État Git final

Les changements présents avant cette phase sont conservés. Aucun commit ni push. conversation_sport_context.py et son test étaient déjà non suivis avant cette phase ; le script de validation et ce rapport sont nouveaux. Résultats détaillés locaux dans reports/phase-1.2a-validation.json et reports/phase-1.2a-validation-initial.json (reports ignoré par Git).

```text
M README.md
 M garmin_coach/coach_chat.py
 M garmin_coach/codex_bridge.py
 M garmin_coach/dashboard.py
 M garmin_coach/runtime_context.py
 M mobile-app/README.md
 M mobile-app/app.json
 M mobile-app/app/index.tsx
?? docs/AVIS_SIMULES_MOTIONEST_2026-10-12.md
?? docs/EVALUATION_MOTIONEST_2026-10-12.md
?? garmin_coach/conversation_sport_context.py
?? scripts/evaluate_motionest.py
?? scripts/validate_conversation_12a.py
?? sites/
?? tests/test_conversation_sport_context.py
```

Phase 1.2B et phase 2 non commencées. Attente de validation.
