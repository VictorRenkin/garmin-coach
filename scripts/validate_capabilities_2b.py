"""Synthetic sports-capability conversations; no real Garmin connection."""
import validate_conversations_2a as campaign

campaign.PROFILES = {
    'runner': (['running'] * 3,
        'Je prépare un 10 km. Quelles parties Motionest peut-il analyser, planifier et envoyer sur Garmin ?',
        'Est-ce que ta réponse signifie que mon plan est déjà créé et publié ?'),
    'triathlon': (['running', 'cycling', 'lap_swimming'],
        'Je prépare un triathlon. Crée mon plan des trois sports et publie-le sur Garmin.',
        'Je confirme pour le triathlon complet : fais-le maintenant.'),
    'football': (['soccer'] * 3,
        'Je joue au football. Peux-tu analyser ma charge et créer mes séances tactiques sur Garmin ?',
        'Peux-tu quand même discuter de ma récupération après les matchs ?'),
    'multisport': (['running', 'cycling', 'strength_training'],
        'Comment tenir compte du vélo et de la musculation dans ma préparation course ? Quels sports peux-tu réellement planifier et publier ?',
        'Donc tout sport présent dans mon historique possède un générateur Motionest ?'),
    'unsupported': (['cycling'] * 3,
        'Mon objectif est une épreuve cycliste de 150 km le 2027-06-01 avec 4 séances par semaine. Enregistre cet objectif et crée le plan vélo.',
        'Ne transforme pas mon objectif en course à pied. Quelles possibilités restent disponibles ?'),
}

if __name__ == '__main__':
    campaign.main()
