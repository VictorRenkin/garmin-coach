# Phase 4D — Lisibilité des réponses du coach

Base : `4de172b0ac2cfd082ef4b3db04122329071d201c`, branche isolée
`codex/motionest-readability-4d`. Deux seuls fichiers de code/tests concernés :
`garmin_coach/coach_chat.py` et `tests/test_coach_readability_4d.py`.
La présentation numérique 4A, les sources historiques et l'interface ne changent pas.

## Origine vérifiée avant correction

- Archive 2A : worktree `motionest-freshness-2a`,
  `reports/conversations-2a.json`, chemin JSON
  `profiles[5].turns[0].message`. Douze contrôles C0 observés : U+0019,
  U+0001 et U+000E. L'exemple `5\x1930 s` est ambigu : retirer le caractère
  fabriquerait `530 s`, sans établir le nombre réellement voulu.
- Archive 2B : worktree `motionest-capabilities-2b`,
  `reports/capabilities-2b-unsupported-final.json`,
  `profiles[0].turns[0].message`. Une paire littérale `\n\n`, aucun vrai LF,
  au milieu d'une réponse ordinaire : « aucun plan n'est créé.\n\nÀ titre de repère… ».
- Ces bancs conservent directement `response.message` renvoyé par le coach.
  Les défauts existent donc avant stockage conversationnel et affichage.
  Les réponses HTTP brutes du fournisseur ne sont pas archivées : on ne peut
  pas départager historiquement génération distante et extraction SDK.
- Reproduction locale sur `main` inchangé, avec SDK simulé :
  `generate_response` accepte et renvoie exactement `5\x1930 secondes.` et
  `Aucun plan créé.\n\nÀ titre de repère, 275 km.`. Aucun appel externe.
- `_json_from_text` utilise `json.loads` ; un contrôle échappé dans le JSON
  est restitué tel que fourni, sans caractère inventé. Le round-trip JSON et
  UTF-8 conserve le texte. `store_response` ne décode pas ces séquences.
  `renderMessages` échappe le contenu pour le HTML et les bulles utilisent
  `white-space: pre-wrap` : aucune preuve d'introduction de ces défauts par l'UI.

Les contrôles textuels existants (`goal_assessment`, `planning_decision`)
rejettent les contrôles dans leurs propres contrats, mais n'offrent pas
d'utilitaire réutilisable pour une réponse conversationnelle multiligne.
Le helper local réutilise les comparaisons de caractères déjà employées,
avec une politique préservant les espaces de mise en forme utiles.

## Correction minimale : refus, sans réécriture

`_validate_message_readability` refuse C0 hors HT/LF/CR ainsi que DEL.
Il conserve accents, caractères combinés, nombres, espaces insécables, emoji,
ZWJ, NEL U+0085 et séparateurs U+2028/U+2029. Aucun décodage `unicode_escape`,
suppression ou remplacement de caractères n'est appliqué.

La forme de paragraphe littéral effectivement observée — ponctuation,
paire `\n\n`, majuscule — est refusée lorsque le contexte n'est pas technique.
Les requêtes portant sur les échappements et les messages contenant code,
guillemets, JSON ou chemins sont conservés littéralement. Cette politique est
volontairement prudente : une paire ambiguë n'est pas interprétée ou convertie.
Elle ne prétend pas classifier parfaitement tout contenu technique libre.

Le contrôle est exécuté dans `_validate_conversation_output`, avant le
réparateur d'action existant pour un fournisseur compatible, puis de nouveau
dans `store_response` avant toute mutation. Les objets produits par
`model_copy` ou `model_construct` ne contournent donc pas la barrière de stockage.
Une consigne demande au modèle de vrais retours à la ligne en prose.

Le flux existant signale une réponse indisponible et permet une relance demandée
par l'utilisateur. Aucun retry IA automatique n'est ajouté. Un texte corrompu
n'est ni stocké comme nouvelle réponse ni transformé en proposition exploitable.
Les valeurs et structures des réponses valides restent identiques.

## Validation

**29 cas nouveaux** : contrôles invalides sur fournisseur natif et compatible,
absence de second appel correctif pour un texte corrompu, refus avant mutation
du stockage, bypass Pydantic, extraction JSON, Unicode légitime, nombres,
retours multiligne, deux tours, isolation Alice/Bob, action et patch conservés,
échappements techniques et paragraphes ordinaires.

- Première collecte : un nom de paramètre pytest réservé (`request`) empêchait
  la collecte ; renommé sans modification applicative.
- Passe suivante : 27 réussis, un faux positif sur « littérale » ; la détection
  du contexte technique accepte désormais les formes féminines et plurielles,
  sans affaiblir le contrôle de l'artefact historique.
- Ciblés élargis : **122 réussis, 0 échec, 0 ignoré**, 20,50 s, avec les tests
  coach, présentation 4A, combinaison numérique, contexte et capacités sportives.
- Dernier garde avant le réparateur compatible ajouté ensuite : **77 réussis,
  0 échec, 0 ignoré**, 3,05 s, sur le code final (`test_coach_readability_4d`
  et `test_coach_chat`). Quatre avertissements Pydantic préexistants seulement.

Preuves ignorées : `reports/phase-4d-targeted-expanded.xml` et
`reports/phase-4d-provider-guard-final.xml`. La suite complète de l'arbre combiné
4C/4D et les éventuels appels IA de combinaison sont réservés à l'orchestrateur
et figurent dans son rapport central, sans résultat présumé ici.

## Limites conservées

Les anciennes archives conversationnelles polluées restent inchangées : aucune
migration, suppression ou réinterprétation d'une action historiquement appliquée.
Un traitement de lecture plus large nécessiterait une politique distincte ; il
n'est pas ajouté discrètement dans cette correction. Le refus nouveau vise le
flux de sortie et de persistance, pas un nettoyage rétroactif.

Aucun appel IA réel par cet agent : les archives et injections déterministes
prouvent les barrières sans chercher à faire produire un contrôle invalide par
un modèle payant. Aucun Garmin réel, push, modification de `main` ou suppression
de sauvegarde/worktree. Les formulations techniques ambiguës restent une limite
explicite plutôt qu'une justification pour altérer du contenu utile.
