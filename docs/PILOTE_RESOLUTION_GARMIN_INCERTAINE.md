# Pilote : spécification de résolution d'un upload Garmin incertain

Base inspectée : `839fd052d610445bad4c71e771a26524deb1cafb`.
Branche documentaire : `codex/motionest-pilot-uncertain-spec`.
Statut : spécification uniquement. Aucun résolveur, endpoint de déblocage,
rapprochement, adoption d'ID ou effacement développé par cette mission.

## Fondations conservées

Références : `STRATEGIE_REPRISE_GARMIN_INCERTAINE.md`,
`GARMIN_INTENTION_PHASE_6_CHECKPOINT.md`, `VALIDATION_3D_HORS_LIGNE.md` et
`SESSION_WEB_PHASE_6_GARMIN_3D.md`.

La stratégie documentaire initiale précédait l'implémentation : ses mentions
« non implémentée » concernant l'intention sont historiques. La base actuelle
écrit `upload_requested_at` dans le même store avant upload ; elle bloque
l'incertitude sans ID, y compris après révision de la même identité de séance.
Le reste de la stratégie, notamment la résolution distante, demeure proposé.

Sources actuelles : `garmin_delivery.publish_accepted_session`,
`assert_upload_retry_safe`, `_uncertain_upload`, `_current_public_record`,
`_public_record` et route POST `/api/garmin/workout-delivery` de `dashboard.py`.
La route refuse le retry incertain avant connexion du client ; publish répète
le contrôle sous le verrou existant. Le rafraîchissement GET lit seulement le
store, sans appel Garmin.

## États observables et transitions permises aujourd'hui

| Faits persistés | État public | Action sûre actuelle | Ce qui reste interdit |
|---|---|---|---|
| Aucune intention ni ID courant | `not_created` / `not_requested` | Envoi explicite avec contrôles et accord courant | Assimiler absence de trace à preuve d'absence d'un ancien upload pré-protection |
| Intention sans ID | `unknown` / `uncertain` | Informer, conserver la trace et le blocage | Renvoyer, effacer la trace, inventer présence ou absence distante |
| ID confirmé et persisté | `created` / `confirmed` | Reprendre les vérifications existantes de cet ID | Créer un nouveau workout sous prétexte de réponse de programmation absente |
| Programmation demandée, calendrier non confirmé | `verification_pending` côté calendrier | Relire l'événement connu selon le chemin existant | Promettre programmation effective ou renvoyer aveuglément |
| Événement exact confirmé, même date | `scheduled` côté calendrier | Retour idempotent sans nouvel effet | Promettre synchronisation ou exécution sur montre |

Une panne après écriture d'intention mais avant POST peut laisser un blocage sans
création distante : c'est le compromis conservateur actuel. Un ID reçu mais non
sauvegardé laisse également l'intention bloquée. Aucun délai d'expiration ne
prouve qu'un upload n'a pas réussi. Révision, nouvel accord ou reconnexion Garmin
ne résolvent pas la tentative précédente.

Pour une intention legacy sans identité stable, la protection peut bloquer les
nouvelles créations de tout le compte. Les IDs courants déjà confirmés restent
reprenables et les autres comptes ne sont pas affectés. Une projection de cette
incertitude ne doit attribuer ni ancienne date ni événement à la séance courante.

## Preuves suffisantes et insuffisantes

Il faut séparer trois questions : existence d'un objet distant, appartenance au
compte, et attribution à cette tentative. Répondre à la première ne résout pas
automatiquement les deux autres.

| Élément observé | Ce qu'il permet d'affirmer | Peut-il débloquer aujourd'hui ? |
|---|---|---|
| ID déjà reçu et sauvegardé par le chemin actuel, relu sur le compte attendu | Objet identifié ; contenu distant à confronter aux faits locaux | Seulement le chemin de reprise existant, jamais une nouvelle création |
| Confirmation distante authentifiée, reliant explicitement requête/tentative et ID au bon compte | Attribution possible si ce mécanisme est réellement documenté et validé | Non : mécanisme et procédure de résolution futurs à spécifier/autoriser |
| ID fourni par l'utilisateur, lecture authentifiée montrant cet objet dans son compte | Existence et appartenance possibles, pas attribution causale à l'intention | Non, pas d'adoption automatique ni de saisie d'ID opérationnelle livrée |
| Contenu, durée, nom ou date identiques, même candidat unique | Ressemblance ; séances légitimement identiques possibles | Non |
| Capture ou affirmation utilisateur « je le vois » | Indice utile à la revue, sans liaison technique vérifiable | Non |
| Calendrier/liste vide, recherche bornée sans résultat, absence sur montre | Aucune preuve d'absence d'un workout non programmé, retardé ou hors page | Non |
| Réponse d'erreur ou timeout sans preuve de non-création | Issue inconnue ; un service peut avoir accepté avant de répondre | Non |
| Preuve locale fiable que le POST n'a jamais pu être émis | Absence d'émission applicative pour cette exécution, si instrumentation et portée établies | Pas de procédure livrée ; ne pas généraliser à retries transport ou autre processus |

Les identifiants locaux et empreintes ne sont pas des clés de déduplication
serveur. La base ne dispose pas d'une corrélation distante garantissant les
critères de la deuxième ligne. Cette spécification ne prétend pas qu'une telle
API existe. La saisie d'un ID et un simple clic « confirmer » ne suffisent donc
pas à rendre un rapprochement sûr.

## Actions UI sûres pour le pilote

Conserver le message générique : « Un précédent envoi Garmin reste incertain ;
aucun nouvel envoi avant vérification. » Aucun bouton de relance pour ce statut.
L'utilisateur peut continuer à consulter Motionest, ses déclarations et les
livraisons déjà confirmées. Rafraîchir la page relit l'état local, sans résoudre
le problème. Une modification de séance ne constitue pas un déblocage.

Actions proposées, non développées : expliquer comment consulter personnellement
Garmin Connect et demander une revue de support. Les libellés doivent préciser
que consulter Garmin ne débloque pas Motionest. Ne pas présenter « supprimer puis
réessayer », « reconnecter pour réparer » ou « j'ai vérifié, renvoyer » comme
solution. Aucun accès distant ne doit être déclenché simplement en ouvrant l'aide.

## Procédure de support proposée

1. Authentifier le demandeur Motionest par le parcours existant. Déterminer le
   compte côté serveur ; ne jamais sélectionner un compte par un `user_id` libre.
2. Consulter en lecture seule la trace concernée et les faits de proposition,
   identité/empreinte de séance, accord et horodatage. Ne jamais demander mot de
   passe Garmin, jetons, cookies ou clé IA. Les captures éventuellement fournies
   doivent être expurgées des autres utilisateurs et données non nécessaires.
3. Expliquer la différence entre état local et objet distant possible. Conserver
   la trace originale ; une note de support n'est pas un ID confirmé de livraison.
4. Si une vérification distante est nécessaire, la planifier comme opération
   distincte, autorisée pour ce compte, avec périmètre lecture seule et preuves
   d'accès/identité définies. Aucune action via un autre compte ou secret partagé.
5. Si les preuves restent ambiguës, conclure « non résolu » et maintenir le
   blocage. L'ancien budget proposé (trois pages de cent, cinq détails) borne une
   éventuelle inspection, sans prouver exhaustivité ni autoriser un nouvel envoi.
6. Ne pas modifier manuellement le JSON, retirer l'intention, adopter un candidat,
   programmer ou supprimer pour clôturer un ticket. Toute résolution exécutable
   exigerait une mission et une procédure distinctes, vérifiées et traçables.

Même avec preuve d'existence, un consentement ancien ne permet pas de programmer
une séance révisée. Toute future action doit relire accord courant, identité,
date, sport et collisions ; la résolution ne doit pas élargir les anciennes
empreintes autorisées ou contourner une décision de refus.

## Opérations futures nécessitant une autorisation distincte

- Choisir le chemin privé actuel ou l'API officielle et obtenir la documentation
  applicable ; ne pas extrapoler un contrat public depuis le SDK.
- Autoriser un compte de test et des lectures distantes bornées, puis qualifier
  les preuves d'appartenance et de corrélation réellement disponibles.
- Définir un protocole de panne réseau et ses limites avant tout upload de test.
  Aucun essai destructif ou entraînement de montre n'est implicite.
- Si une adoption d'ID ou une clôture de tentative est envisagée, décider le
  contrat, les acteurs habilités, les effets permis et l'audit avant développement.
  La présente spécification ne donne pas cette autorisation.

## Critères d'acceptation objectivables

Pour une future mission de résolution, commencer par une décision de faisabilité :
sans preuve technique d'attribution suffisante, le résultat attendu reste le
blocage, pas une procédure qui le contourne.

| Cas à vérifier | Résultat attendu |
|---|---|
| Réponse perdue, redémarrage, deux threads | Une intention conservée ; aucune deuxième émission applicative sous les hypothèses actuelles |
| Révision même identité, date changée, fallback legacy | Toujours incertain ; aucune attribution d'ancienne date/ID/événement à la carte courante |
| ID courant déjà confirmé malgré autre intention legacy | Reprise existante permise, sans nouvel upload |
| Autre utilisateur, ID étranger, demande anonyme | Aucune lecture ni mutation croisée ; aucune décision fondée sur l'identité fournie par le client |
| Recherche vide, ambiguë, malformée ou hors budget | Non résolu, trace inchangée, zéro effet distant |
| Revue support et rafraîchissement UI | Pas de mutation du store, pas de connexion ni publication automatique |
| Future attribution suffisante, si mécanisme établi | Gardes métier relues ; aucun effet supplémentaire hors contrat approuvé ; preuve et trace d'origine conservées |
| Affichage d'incertitude | Message clair, aucun bouton de renvoi, aucun secret ou détail fournisseur brut |

Les tests existants `test_garmin_upload_uncertain.py` (22 cas sur la livraison C),
`test_web_journey_phase6.py`, `test_web_ui_phase6.py`, `test_garmin_delivery.py`
et `test_complex_double_day_3d.py` prouvent les protections simulées pertinentes.
Aucun nouveau test de faisabilité n'est nécessaire pour ce livrable documentaire :
un faux serveur ne démontrerait pas l'existence d'une preuve distante réelle.
Aucun test relancé ici ; les résultats cités sont antérieurs, pas une campagne nouvelle.

## Décisions encore nécessaires et limites

Décider du pilote (blocage informatif seulement ou étude d'une résolution future),
du support habilité, du compte de test explicitement autorisé et du niveau de
preuve acceptable pour l'API retenue. Sans ces éléments, aucune levée du blocage.
Ne pas promettre de délai automatique de résolution.

Anciennes tentatives sans trace indétectables ; retries internes SDK non prouvés ;
verrou intra-processus seulement ; fsync/coupure électrique non garanti ; aucune
garantie exactly-once serveur, réception montre ou export d'horaires. L'absence
de résolveur est volontaire et doit être annoncée honnêtement au pilote.

Un seul document ajouté. Aucun code applicatif, route, test, secret, mobile,
compte Garmin ou sauvegarde modifié ; aucune IA payante, recherche distante,
suppression, réconciliation, push ou modification de main.
