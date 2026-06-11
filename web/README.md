# MatchNest Web PWA

React + TypeScript + Vite frontend for MatchNest.

## Run

```bash
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

On another device in the same Wi-Fi network, open the computer LAN URL:

```text
http://<computer-lan-ip>:5173
```

The app expects the backend at:

```text
http://127.0.0.1:8000
```

You can change the backend URL inside the Settings tab.

## Build

```bash
npm run build
```

## PWA notes

- `manifest.webmanifest` defines the Home Screen app metadata.
- `public/sw.js` caches the app shell and recently fetched GET requests.
- On iPhone, open the deployed URL in Safari and use Add to Home Screen.
