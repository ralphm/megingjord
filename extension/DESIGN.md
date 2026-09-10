# Megingjord Google Meet Extension

Firefox-first browser extension providing Google Meet state and control to
[Megingjord](https://github.com/ralphm/megingjord) over a local WebSocket.

## Architecture

```
Meet tab (content script)          Extension background script          Megingjord
┌─────────────────────────┐   ┌──────────────────────────────┐   ┌──────────────────┐
│ MutationObserver        │   │ owns WebSocket (never        │   │ aiohttp WS server │
│ + 1s polling fallback  │──▶│ throttled, survives tab      │──▶│ default port 2394 │
│ + visibilitychange sync│   │ switches)                    │   │ (configurable)    │
└─────────────────────────┘   └──────────────────────────────┘   └──────────────────┘
        runtime.sendMessage            ws://127.0.0.1:2394 (default)
```

The WebSocket lives in the background script, not the content script, so it:

- is not throttled when the Meet tab is in the background
- is not subject to page-level ad blockers
- does not trigger the browser's localhost permission prompt
- stays connected regardless of how many Meet tabs are open

Note: Firefox's default Manifest V3 CSP includes `upgrade-insecure-requests`,
which would upgrade the `ws://` connection to `wss://`. The manifest therefore
overrides the CSP with an explicit `connect-src ws://127.0.0.1:*`.

The WebSocket URL is configurable via the extension options page (default
`ws://127.0.0.1:2394`); it must match the `google_meet` section's `host` and
`port` in the Megingjord configuration. Firefox match patterns do not support
ports, so the manifest's `host_permissions` use `ws://127.0.0.1/*`, which
matches any port on localhost (the port is ignored in matching).

## Protocol

JSON messages over `ws://127.0.0.1:2394`.

### Extension -> Megingjord

| Event | Payload | Notes |
|---|---|---|
| `phase` | `{"event": "phase", "phase": "lobby"\|"green_room"\|"green_room_switch"\|"meeting"\|"exit_hall"\|"none", "pending": bool}` | Sent on change and on (re)connect; `green_room_switch` is the green room while a call runs on another device; `none` means no Meet tab is open; `pending: true` marks the phase a deck command leads to, before the DOM confirms it |
| `micMutedState` | `{"event": "micMutedState", "muted": bool}` | |
| `cameraMutedState` | `{"event": "cameraMutedState", "muted": bool}` | |
| `handMutedState` | `{"event": "handMutedState", "muted": bool}` | `muted` means hand not raised |
| `enterReady` | `{"event": "enterReady", "ready": bool}` | Green room: join button clickable |
| `enterLabel` | `{"event": "enterLabel", "label": str}` | Green room: join button text |
| `hasNextMeeting` | `{"event": "hasNextMeeting", "hasNextMeeting": bool}` | Lobby: scheduled meeting present |
| `subtitle` | `{"event": "subtitle", "control": str, "subtitle": str}` | Tile subtitle, e.g. meeting title for `start-next` / `enter` |

### Megingjord -> Extension

| Event | Action |
|---|---|
| `toggleMic` | Toggle microphone |
| `toggleCamera` | Toggle camera |
| `toggleHand` | Toggle raised hand |
| `leaveCall` | Leave the call (handles the confirmation dialog) |
| `startInstantMeeting` | Start an instant meeting (lobby) |
| `startNextMeeting` | Start the next scheduled meeting (lobby) |
| `enterMeeting` | Join now (green room) |
| `switchHere` | Switch the call to this device (green room, while a call runs on another device) |
| `rejoin` | Rejoin the meeting (exit hall) |
| `returnHome` | Return to the Meet home screen (green room / exit hall) |

## Button matrix

The button layout is owned by Megingjord; the extension only reports phase
and state and executes commands.

| Phase | Keys |
|---|---|
| `lobby` | start-instant, start-next (calendar-remove icon when no scheduled meeting) |
| `green_room` | mic, camera, home, enter (dimmed until join button ready) |
| `green_room_switch` | mic, camera, switch, enter (dimmed until join button ready) |
| `meeting` | mic, camera, hand, leave |
| `exit_hall` | home, rejoin |

## Immediate phase reporting

The URL changes before the new page renders. Phase changes are reported
immediately from the action that caused them:

- A deck command reports the phase it leads to (e.g. `startNextMeeting`
  reports `green_room`, `leaveCall` reports `exit_hall`) with
  `pending: true`, so the tiles react without waiting for the page to
  render. Megingjord renders the pending phase's tiles inactive until
  the DOM confirms the phase (`pending: false`).
- While a phase is pending, reports of any other phase (stale DOM
  reads, the URL watcher's `lobby` after leaving a call) are
  suppressed; the intent stands until the DOM confirms the expected
  phase or a 5 s timeout falls back to the DOM.
- The URL watcher reports `lobby` for lobby paths (`/`, `/home`,
  `/landing`), covering navigations not caused by a deck command.

The DOM-based detection refines the phase once the page renders (e.g.
`green_room` vs `meeting`).

## State forwarding

The background script merges per-tab state and forwards only changed
fields to Megingjord, so a state update does not re-send every event.
After a phase change the full state is re-forwarded, so Megingjord
does not miss the mute states that follow.

Fields that do not apply to the current phase are reported as
undefined and clear the tab's known values, so stale states (e.g. the
mute states in the exit hall) are not forwarded.

## Multi-tab policy

The background script tracks state per tab and reports the "best" tab:
`meeting` > `green_room` = `green_room_switch` > `lobby` > `exit_hall`, tie-broken by most
recent activity. Commands from Megingjord are routed to the best tab.

## Selectors

Phase and control selectors are derived from:

- [petele/StreamDeck-Meet](https://github.com/petele/StreamDeck-Meet)
  (phase model: lobby / green room / meeting / exit hall)
- [ChrisRegado/streamdeck-googlemeet](https://github.com/ChrisRegado/streamdeck-googlemeet)
  (post-September-2024 Meet redesign selectors for mic/camera/hand/leave)

Google changes the Meet DOM regularly; selectors may need updating. The
content script logs a warning when a command's button cannot be found.

## Install

Personal use, unsigned:

1. In Firefox `about:config`, set `xpinstall.signatures.required` to `false`.
2. Zip the extension files (`manifest.json`, `background.js`, `meet.js`,
   `options.html`, `options.js`).
3. In `about:addons`, use the settings cog -> "Install Add-on From File...".

For temporary testing, load via `about:debugging` -> "Load Temporary Add-on".

## Development

No build step. Edit the files and reload the extension in
`about:debugging`.
