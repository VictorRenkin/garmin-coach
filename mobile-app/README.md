# Garmin Coach Mobile

Application Expo pour Android et iPhone. Elle se connecte au moteur Garmin Coach exécuté sur l'ordinateur du même réseau Wi-Fi.

## Essai sur téléphone

1. Sur l'ordinateur, lancer `..\demarrer-app-mobile.cmd`.
2. Installer **Expo Go** sur le téléphone.
3. Dans ce dossier : `pnpm install`, puis `pnpm start`.
4. Scanner le QR code avec Expo Go.
5. Dans Réglages, saisir l'adresse et le jeton affichés sur l'ordinateur.

## APK Android installable

Après connexion à un compte Expo :

```powershell
npx eas-cli build --platform android --profile preview
```

Le profil `preview` produit un APK téléchargeable et installable. Pour iPhone, Apple impose un compte développeur et une signature iOS.

La clé OpenAI n'est jamais stockée dans l'app : configure-la sur l'ordinateur avec `..\configurer-ia.cmd`.
