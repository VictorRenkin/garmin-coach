# Audit fonctionnel Garmin Coach

Date : 27 juillet 2026

## Périmètre

- Coach : envoi, réponses locales et OpenAI simulées, mémoire, ressentis,
  objectifs, reports, confirmations, erreurs, délais et concurrence.
- Tableau de bord : objectif, activités, détails, graphiques, zones, tours,
  plan, historique et affichage responsive.
- Backend : routes HTTP, validation, fichiers absents ou invalides, protection
  des chemins et confidentialité.
- Projet : tests, dépendances, compilation Python, JavaScript et état Git.

Les tests navigateur et HTTP ont utilisé un environnement temporaire contenant
une course et un plan fictifs. Aucune activité ni séance Garmin réelle n'a été
modifiée.

## Défauts importants détectés et corrigés

1. **Route du coach cassée** : la génération de réponse se trouvait dans la
   route de l'objectif. `/api/coach/messages` fermait donc la connexion avec une
   variable `answer` inexistante. Le bloc est replacé et couvert par un test HTTP
   complet.
2. **Coach local répétitif** : de nombreuses questions recevaient la même
   réponse générique. Des réponses spécifiques couvrent désormais douleur,
   urgence potentielle, fatigue, sommeil, ressenti facile/normal/difficile,
   charge, vacances, séance ratée, cardio, allure aberrante et calendrier.
3. **Objectif décoratif figé** : le grand « 21.1 » restait visible après un
   changement de distance. Il suit maintenant l'objectif enregistré.
4. **Anciens détails GPS** : les nouveaux imports étaient nettoyés, mais un
   ancien fichier local pouvait encore exposer des clés de position dans l'API.
   Le nettoyage est maintenant aussi appliqué au moment de la lecture.
5. **Faux sentiment de connexion à ChatGPT** : sans clé API, les réponses
   locales étaient présentées comme si elles venaient de ChatGPT. L'écran
   indique maintenant clairement le mode utilisé, permet de configurer la clé
   sans terminal et marque chaque réponse `ChatGPT` ou `Mode local`.
6. **Discussion sans effet sur les entraînements** : le coach savait changer
   un objectif ou déplacer une séance, mais pas adapter la semaine. Il peut
   désormais proposer un allègement de 20 %, une progression prudente de 10 %
   ou une semaine de récupération. La confirmation dans le chat modifie toutes
   les séances futures concernées.
7. **Chat décentré** : la colonne d'introduction déséquilibrait la zone de
   conversation. Le coach est maintenant une carte unique de 900 px maximum,
   centrée sur grand écran et fluide sur mobile.

## Résultats

- Suite Python : **102 tests réussis**.
- Scénarios du coach : salutations, aide, dernière course, trois niveaux de
  ressenti, douleur, douleur thoracique, 10 km, semi-marathon, fréquence,
  date cible, report précis ou ambigu, charge, sommeil, vacances, cardio et
  valeur d'allure impossible.
- HTTP : réponses attendues `200/201/400/404`, coach local autour de 43 ms côté
  serveur, limites de message validées et chemins de rapports protégés.
- Concurrence : 20 requêtes parallèles, 20 réponses liées correctement,
  identifiants uniques et aucun message restant en attente.
- Navigateur : Entrée envoie, Maj+Entrée crée une ligne, confirmation d'objectif
  persistée, configuration OpenAI invalide expliquée, proposition d'allègement
  produite puis appliquée à trois séances, et aucune erreur JavaScript observée.
- Responsive : 390, 768 et 1440 px sans débordement horizontal ; trois séances
  et chat visibles à chaque largeur.
- Graphiques : bascule allure/cardio/altitude et masquage d'un point aberrant
  vérifiés.
- Confidentialité : aucun secret suivi par Git ; identité et GPS retirés des
  contextes et des détails publics.
- Dépendances : `pip check` ne signale aucun conflit.

## Limites de l'audit

- Aucune clé n'est actuellement configurée dans `.env`. Le chemin OpenAI est
  couvert avec des clients simulés (succès et indisponibilité), tandis que le
  mode local a été testé réellement de bout en bout.
- Les opérations Garmin qui modifieraient le compte réel sont testées avec de
  faux clients. Elles n'ont volontairement pas été exécutées sur le calendrier
  personnel pendant l'audit.
- Quatre avertissements proviennent de modèles Pydantic internes à la dépendance
  `garminconnect`; ils n'entraînent aucun échec fonctionnel actuel.
