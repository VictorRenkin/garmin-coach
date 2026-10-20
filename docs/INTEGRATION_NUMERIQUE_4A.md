# Intégration isolée des corrections numériques 4A

Le 20 octobre 2026, branche `codex/motionest-integrate-numeric-4a`, issue de `main` propre à `ea52e6bfe6d5dfc7102913cc3652444079cec6a8`. Protection initiale confirmée par l'orchestrateur avant toute fusion. La livraison 4A existante `ebd2580084be62a2048d6101904fd8ea191f6d6a` est réutilisée sans réimplémentation ni cherry-pick.

## Fusion réelle et périmètre

Commit de fusion `c0de7d46d8de39e83fe771e11ca6d5fcab0a008a`, parents exacts :

- `ea52e6bfe6d5dfc7102913cc3652444079cec6a8` : livraison 3C;
- `ebd2580084be62a2048d6101904fd8ea191f6d6a` : livraison numérique 4A.

Arbre `7f8be474750ac3205950e211ffab4d40945a15b3`, identique à la simulation de préparation. Fusion automatique sans conflit; aucun remplacement du fichier coach par sa version ancienne. Le diff applicatif depuis 3C correspond exactement aux cinq fichiers 4A et 482 insertions : helper de présentation, 18 lignes du coach, tests numériques, script de validation et rapport original. Aucun correctif supplémentaire n'était nécessaire pour obtenir ce checkpoint.

`CalendarRequest` et ses interdictions d'action automatique, les blocs structurés 3B, les identifiants et accords 3C restent présents. L'enrichissement numérique intervient après la recherche historique existante et avant encodage du payload des deux chemins fournisseur. Les sources, fenêtres 28/84, projection historique, imports et code Garmin ne changent pas lors de cette fusion.

## Validation ciblée du checkpoint

**206 tests réussis, 0 échec, 0 ignoré en 22,49 s.** Quatre avertissements Pydantic préexistants du SDK tiers. Commande :

```text
python -m pytest -q tests/test_coach_numeric_presentation.py tests/test_coach_chat.py tests/test_conversation_sport_context.py tests/test_conversation_history_search.py tests/test_conversational_memory.py tests/test_session_blocks_3b.py tests/test_complex_sessions_quality.py tests/test_calendar_3c.py tests/test_calendar_3c_quality.py --junitxml=reports/integration-4a-targeted.xml
```

Les cas numériques préexistants vérifient 37 800 s = 10 h 30, 109 800 s = 30 h 30, moyennes calendaires exactes, null distinct de zéro et absence de mutation des distances/états d'import. Les modules conversationnels, blocs et calendrier protègent leur continuité sur l'arbre combiné. Le helper réutilise `summary._duration`, ne lit aucune activité et ne calcule aucune nouvelle charge physiologique. Les distances demeurent propres à chaque discipline.

## Budgets : portée exacte des contrôles

Les constantes canoniques demeurent `MAX_CONTEXT_BYTES=48_000` et `MAX_SEARCH_BYTES=24_000`. Leurs troncatures ne sont pas modifiées. `presentation_numerique` est ajouté ensuite au payload modèle : **ces budgets ne sont pas une limite globale du payload modèle**. La régression existante borne à six synthèses et mesure le complément sous 4 Kio dans ses fixtures.

Mesure locale supplémentaire sans IA : sept séances V2 validées, projection calendaire locale, intention `CalendarRequest` conservée dans la conversation, recherche à deux périodes et deux synthèses hebdomadaires. JSON UTF-8 compact : 6 029 octets avant, 7 602 après; complément 1 547 octets, delta total 1 573 octets avec sa clé. Les synthèses canoniques occupent respectivement 984 octets et la recherche 379 dans cette fixture. Il s'agit d'une mesure représentative, **pas d'une saturation des budgets 48/24 Kio ni d'une borne universelle**. Aucun dépassement d'un contrat existant n'est établi et aucun relèvement de plafond n'est appliqué. Preuve ignorée par Git : `reports/integration-4a-budget-measurement.json`.

## Défaut combinatoire préexistant révélé par les tests 3D

La qualité 3D a ensuite reproduit un défaut distinct de 4A : après livraison simulée de deux séances complexes, réviser seulement la séance du matin laisse son ancien workout dans le calendrier; la vérification de collisions ne le reconnaît plus sous la nouvelle empreinte et bloque même la simple confirmation de la séance du soir inchangée, encore acceptée. Checkpoint rouge de C `0f1b369` : sept cas réussis, un échoué, conservé comme preuve; tests/document repris dans cette branche par `45c0aab`.

Correction minimale autorisée après revue : `5230807e17729e127e0d80ea9eeb4e6fbeab2aaa`. Le parsing strict existant du calendrier est extrait sans assouplissement. Après les gardes d'identité, accord, date et sport, une confirmation sans effet distant est possible uniquement pour une clé de livraison déjà persistée sous l'empreinte courante, un ID connu, une demande de programmation antérieure, la même date et un événement workout exactement correspondant dans un calendrier strictement interprétable. L'horodatage est conservé si les faits ne changent pas. Aucun ancien ID n'est ajouté à la liste des livraisons autorisées; toute nouvelle création/programmation demeure soumise aux collisions.

Validation après correction : **44 réussis, 0 échec, 0 ignoré en 7,06 s** (`test_complex_double_day_3d.py`, `test_delivery_confirmation_guard.py`, `test_garmin_delivery.py`, `test_calendar_3c_quality.py`). Les huit cas de C passent, y compris le refus du matin révisé avant nouvel accord puis son refus pour collision après accord. Les quatre nouveaux cas A préservent le refus de calendriers absents/malformés et d'un upload connu sans historique de programmation en présence d'une collision. Le contrôle des opérations nouvelles n'est pas contourné par la confirmation du soir.

## Coordination et limites

L'orchestrateur coordonne la qualité indépendante, les scénarios 3D hors ligne et une seule suite complète de l'arbre final. Les résultats de ces étapes ne doivent pas être déduits du succès des 206 tests ci-dessus. Aucun appel IA n'est effectué par l'agent d'intégration; les résultats IA originaux de 4A ne constituent pas une nouvelle validation de combinaison.

La nouvelle campagne de combinaison est exécutée et relue par l'orchestrateur avec le banc indépendant de B : **6/6 appels techniquement et fonctionnellement réussis**, modèle effectivement routé `gpt-5.6-luna`, 62 347 tokens d'entrée + 1 567 de sortie = **63 914 tokens**. Cas : chiffres 28/84 et moyennes exactes; triathlon 12/60/3 km séparés; import interrompu explicitement limité; double journée de 60 minutes prévues sans charge physiologique inventée; patch de trois blocs exécutant 1 920 secondes; maintien des chiffres au second tour de conversation. Comptes et activités synthétiques exclusivement, zéro opération Garmin réelle. Ces résultats concernent ces hypothèses, sans garantie universelle sur les réponses futures.

L'ambiguïté connue de complétude d'import et les caractères de contrôle restent documentés hors périmètre. Les horaires des doubles journées restent internes à Motionest : la livraison Garmin transmet une date, pas une heure. Aucun compte ni appareil Garmin réel n'est utilisé.

Réserve 3D conservée : si un upload réussit fictivement à distance mais que sa réponse ne confirme aucun ID, Motionest ne programme rien et ne prétend pas disposer d'un ID; une nouvelle tentative peut néanmoins créer un deuxième workout distant. Cette incertitude antérieure de création sans accusé exploitable n'est pas résolue par le correctif de confirmation et ne justifie pas ici un nouveau moteur de livraison.

Les résultats de la suite complète sur le SHA final sont consignés séparément dans le rapport central `docs/SESSION_AUTONOME_4A_3D.md` de l'orchestrateur. Le présent document est commité avant cette suite pour que l'arbre validé et l'arbre proposé à l'intégration soient identiques; aucun résultat complet n'est anticipé ici. `main` n'est pas modifié par l'agent d'intégration. Aucun push, suppression de sauvegarde ou de worktree.
