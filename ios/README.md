# MatchNest iOS

SwiftUI source files for the native iPhone app and WidgetKit extension.

## Targets to create in Xcode

Create an iOS app named `MatchNest`, then add:

- App target: include files under `ios/MatchNest`
- Widget extension target: include files under `ios/MatchNestWidget`
- App Group capability for both targets: `group.matchnest`

The widget reads the next event from shared `UserDefaults` under key `next_event`.

## App group

Both the app target and widget extension target must use:

```text
group.matchnest
```

## Backend URL

`MatchNestAPI.swift` currently points to:

```swift
http://127.0.0.1:8000
```

On a real iPhone, replace this with a reachable LAN or production URL.

See `../docs/SETUP_MAC.md` for the full Mac and iPhone setup flow.
