# Mac and iPhone Setup

## 1. Clone the repository

```bash
git clone <your-repo-url>
cd match-nest
```

## 2. Configure backend environment

```bash
cp .env.example .env
```

Keep this file at the repository root:

```text
match-nest/.env
```

Do not create a second `backend/.env`.

Fill in tokens when you have them:

```text
FOOTBALL_DATA_TOKEN=...
PANDASCORE_TOKEN=...
```

F1 works without a token.

## 3. Run the backend on Mac

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:

```text
http://127.0.0.1:8000/docs
```

## 4. Create the Xcode project

1. Open Xcode.
2. Create a new iOS App project named `MatchNest`.
3. Use Swift and SwiftUI.
4. Add the Swift files from `ios/MatchNest` to the app target.
5. Add a Widget Extension target named `MatchNestWidget`.
6. Add the Swift files from `ios/MatchNestWidget` to the widget target.
7. Add App Groups capability to both targets.
8. Use the same app group in both targets:

```text
group.matchnest
```

## 5. Point the iPhone app at the backend

In `ios/MatchNest/Services/MatchNestAPI.swift`, replace:

```swift
http://127.0.0.1:8000
```

with the Mac LAN address, for example:

```swift
http://192.168.1.20:8000
```

Your iPhone and Mac must be on the same network.

## 6. Run on iPhone

1. Connect the iPhone to the Mac.
2. Select the iPhone as the run destination in Xcode.
3. Press Run.
4. Add the MatchNest widget from the iPhone Home Screen widget picker.

## 7. Later: TestFlight

Use TestFlight only after the app builds locally and the backend has a stable reachable URL.
