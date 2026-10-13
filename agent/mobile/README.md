# Veyra Expo Go client

See the Veyra web app’s **iPhone app** page (`/mobile`) for installation,
network security, testing, and limitations. No paid service or Apple account
is needed. This project uses Expo SDK 57; use an SDK-compatible Expo Go.

From this directory, with Node 22+:

```sh
npm ci
npm start
```

Scan the QR code on a phone on the same trusted Wi-Fi. Enter the Veyra
computer’s private IP URL (port 3000) and its network password, not model
credentials. Stop Metro with Ctrl+C. No background daemon is installed.

Checks: `npm test`, `npm run check`, `npm run export:ios`.
The app uses session-scoped event polling, plain text, image display and
explicit per-request command approvals. It does not upload attachments,
load older sessions, manage model credentials, or offer push notifications.
A failed send keeps the draft and is never retried automatically.

Dependency audit: the current Expo SDK dependency tree reports 22 advisories
(15 high, 7 moderate), in development/build tooling including braces,
node-forge and uuid. `npm audit fix` does not resolve them; the suggested
forced Expo downgrade is incompatible with this SDK. Keep Metro on a trusted
network and stop it when not in use. Do not treat this development client as
a production-hardened release.
