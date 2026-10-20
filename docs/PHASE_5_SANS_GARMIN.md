# Phase 5 — Découverte explicite sans connexion Garmin

Base : `9a2c5159f4b8ed351cf74cf293214938336ead90`. Branche isolée : `codex/motionest-discovery-phase5`. Les préparations de découverte sont réutilisées; aucun nouveau compte, profil, moteur historique, générateur ou système de confiance n'est créé.

## Parcours ajouté et fondations réutilisées

Un compte Motionest vérifié peut choisir « Continuer sans Garmin » sur la page de connexion Garmin. Le POST protégé `/api/onboarding/continue-without-garmin` ignore toute identité fournie dans son corps et utilise exclusivement l'utilisateur de la session. Il vérifie aussi la confirmation du compte côté serveur; la simple redirection GET ne sert pas de garde d'autorisation.

`onboarding.enable_discovery_without_garmin` persiste uniquement le choix `discovery_without_garmin=True` dans le fichier existant, sans toucher au profil, aux étapes déclarées, aux imports ou aux activités. `determine_user_stage` et `build_first_run_journey` ouvrent alors le dashboard sans tokens. Les règles de vérification du compte restent prioritaires. Si des tokens Garmin sont présents ultérieurement, le drapeau est ignoré et le parcours Garmin existant reprend, y compris ses étapes d'import ou d'échec : un choix ancien ne masque pas ces états.

Le dashboard réutilise ses vues, le formulaire V2 de déclarations et le coach existants. Un encart explique qu'aucune nouvelle activité Garmin n'est importée dans ce mode et qu'un éventuel historique local est conservé. Il propose l'éditeur de profil/préférences et le lien pour connecter Garmin plus tard. Le bouton de synchronisation est masqué dans cette découverte. Le GET `/connect-garmin` reste accessible depuis cet état; il ne redirige donc pas indéfiniment vers le dashboard.

`coach_context.connexion_sportive` expose deux booléens factuels : `garmin_connected` et le choix de découverte effectivement actif sans tokens. Ces champs indiquent la connexion actuelle, **pas l'absence d'historique local**. Les objectifs et préférences déclarés, la mémoire conversationnelle, les métriques réelles, les limites 4C et les conversions 4A restent celles des systèmes existants. Le coach reste soumis à sa configuration IA et à ses droits habituels; aucun modèle local gratuit ou faux historique n'est ajouté.

## Gardes et connexion tardive

En découverte sans tokens, les routes de synchronisation, livraison structurée, décision legacy susceptible de publier et déplacement Garmin refusent l'action avant tout appel à `connect` ou au setup. Les actions de plan legacy du coach sont également bloquées dans ce mode; le profil conserve son parcours de modification existant. Le choix n'active aucun import et n'accepte aucune séance à la place du sportif.

La connexion Garmin ultérieure reste une action explicite sur le formulaire existant. Le même identifiant utilisateur et ses déclarations/mémoires sont conservés; aucune copie ou migration de données de démonstration n'est nécessaire. Le setup Garmin existant continue de préserver les données et statuts reconnus. La démonstration interactive optionnelle n'est pas développée : aucune activité synthétique n'est introduite dans un historique réel.

## Coordination, fichiers et validation ciblée

Le backend sans Garmin est sauvegardé dans `9967e07`. La livraison C `0777edb` est fusionnée réellement par `0149542` avant toute écriture A dans `dashboard.py` ou `dashboard_v2.html`. Les corrections favicon et répartition vide sont conservées. Le checkpoint backend/UI est `1c928557b9559ccd4b8162aa9188dcf39bebbb18`.

Fichiers A : `onboarding.py`, `user_journey.py`, `first_run_journey.py`, `dashboard.py`, `dashboard_v2.html`, `connect_garmin.html`, `tests/test_discovery_phase5.py` et ce rapport. Les fichiers qualité/UI propres à C restent distincts.

- Huit nouveaux cas backend : choix explicite, vérification e-mail prioritaire, profil/étapes préservés, absence d'activités créées, retour au parcours Garmin selon ses quatre statuts et isolation entre comptes.
- Suite ciblée au checkpoint UI : **59 réussis, 0 échec, 0 ignoré en 15,55 s**, quatre avertissements Pydantic préexistants. Modules : `test_discovery_phase5.py`, `test_user_journey.py`, `test_first_run_journey.py`, `test_ui_phase6_fixes.py`, `test_onboarding_v2.py`. Preuve : `reports/discovery-ui-targeted.xml`.
- Une première assertion de fixture a été corrigée : le créateur de dossiers existant peut créer un répertoire d'historique vide; le test vérifie donc l'absence de fichiers d'activités, pas l'absence du dossier. Aucun comportement applicatif n'a été modifié pour satisfaire cette assertion.

La qualité indépendante vérifie les vraies routes avec comptes synthétiques, cookies, données temporaires et clients simulés. Les validations navigateur desktop/mobile, appels IA synthétiques mesurés et suite complète finale sont coordonnés par l'orchestrateur; leurs résultats seront rapportés séparément, sans anticiper une réussite ici. L'agent A ne réalise aucun appel IA ou Garmin réel.

`main` reste inchangé pendant cette livraison, aucun push ni suppression de branche, sauvegarde ou worktree. Cette phase n'étend pas les sports générables, n'ajoute aucun connecteur et ne modifie pas les fenêtres 28/84 jours.
