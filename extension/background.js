// Megingjord Google Meet extension - background script.
//
// Owns the WebSocket connection to Megingjord (ws://127.0.0.1:2394) and
// aggregates state from all Google Meet tabs. Keeping the socket in the
// background script means it is not throttled when a tab is in the
// background, and it is not subject to page-level ad blockers or the
// browser's localhost permission prompt.

const DEFAULT_WS_URL = "ws://127.0.0.1:2394";
let wsUrl = DEFAULT_WS_URL;
const RECONNECT_INTERVAL_MS = 2000;

// Phases with higher priority win when multiple Meet tabs are open.
const PHASE_PRIORITY = {
  meeting: 3,
  green_room: 2,
  green_room_switch: 2,
  lobby: 1,
  exit_hall: 0,
};

let socket = null;
let reconnectTimer = null;

// tabId -> {phase, micMuted, cameraMuted, handMuted, updated}
const tabStates = new Map();

// Values last forwarded to Megingjord for the current best tab.
let lastForwarded = null; // { tabId, values }

function connect() {
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log("Megingjord Meet: connected to Megingjord");
    // Megingjord resets its state on reconnect; re-forward everything.
    lastForwarded = null;
    if (tabStates.size === 0) {
      // No Meet tabs open; make sure Megingjord does not keep stale keys.
      sendToMegingjord({ event: "phase", phase: "none" });
      return;
    }
    // Ask every Meet tab for its current state; the best tab's state will
    // be forwarded once the responses come in.
    for (const tabId of tabStates.keys()) {
      browser.tabs.sendMessage(tabId, { type: "getState" }).catch(() => {});
    }
  };

  socket.onclose = () => {
    console.log("Megingjord Meet: disconnected, reconnecting");
    socket = null;
    reconnectTimer = setTimeout(connect, RECONNECT_INTERVAL_MS);
  };

  socket.onerror = () => {
    socket.close();
  };

  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    const tabId = bestTabId();
    if (tabId !== null) {
      browser.tabs
        .sendMessage(tabId, { type: "command", event: message.event })
        .catch(() => {});
    }
  };
}

function bestTabId() {
  let best = null;
  let bestPriority = -1;
  let bestUpdated = 0;
  for (const [tabId, state] of tabStates) {
    const priority = PHASE_PRIORITY[state.phase] ?? -1;
    if (
      priority > bestPriority ||
      (priority === bestPriority && state.updated > bestUpdated)
    ) {
      best = tabId;
      bestPriority = priority;
      bestUpdated = state.updated;
    }
  }
  return best;
}

function sendToMegingjord(message) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

// Forward the state of the given tab if it is the best tab. Only
// changed fields are sent; after a phase change the full state is
// re-forwarded so Megingjord does not miss the mute states.
function forwardState(tabId) {
  if (tabId !== bestTabId()) {
    return;
  }
  const state = tabStates.get(tabId);
  if (!state) {
    return;
  }
  if (!lastForwarded || lastForwarded.tabId !== tabId) {
    lastForwarded = { tabId, values: {} };
  }
  const previous = lastForwarded.values;
  const phaseChanged =
    state.phase !== previous.phase || state.pending !== previous.pending;
  const changed = {};
  for (const [key, value] of Object.entries(state)) {
    if (key === "updated" || value === undefined) {
      continue;
    }
    if (phaseChanged || value !== previous[key]) {
      changed[key] = value;
    }
  }
  if (Object.keys(changed).length === 0) {
    return;
  }
  lastForwarded.values = { ...previous, ...changed };
  if (
    state.phase !== undefined &&
    (changed.phase !== undefined || changed.pending !== undefined)
  ) {
    sendToMegingjord({
      event: "phase",
      phase: state.phase,
      pending: state.pending === true,
    });
  }
  if (changed.micMuted !== undefined) {
    sendToMegingjord({ event: "micMutedState", muted: state.micMuted });
  }
  if (changed.cameraMuted !== undefined) {
    sendToMegingjord({ event: "cameraMutedState", muted: state.cameraMuted });
  }
  if (changed.handMuted !== undefined) {
    sendToMegingjord({ event: "handMutedState", muted: state.handMuted });
  }
  if (changed.enterReady !== undefined) {
    sendToMegingjord({ event: "enterReady", ready: state.enterReady });
  }
  if (changed.enterLabel !== undefined) {
    sendToMegingjord({ event: "enterLabel", label: state.enterLabel });
  }
  if (changed.hasNextMeeting !== undefined) {
    sendToMegingjord({
      event: "hasNextMeeting",
      hasNextMeeting: state.hasNextMeeting,
    });
  }
  if (changed.nextMeetingTitle !== undefined) {
    sendToMegingjord({
      event: "subtitle",
      control: "start-next",
      subtitle: state.nextMeetingTitle,
    });
  }
  if (changed.meetingTitle !== undefined) {
    sendToMegingjord({
      event: "subtitle",
      control: "enter",
      subtitle: state.meetingTitle,
    });
  }
}

browser.runtime.onMessage.addListener((message, sender) => {
  if (!sender.tab || message.type !== "state") {
    return;
  }
  // Structured clone preserves undefined (unlike JSON); drop it so a
  // mid-transition read cannot clobber known values like the phase.
  const state = {};
  for (const [key, value] of Object.entries(message.state)) {
    if (value !== undefined) {
      state[key] = value;
    }
  }
  tabStates.set(sender.tab.id, {
    ...(tabStates.get(sender.tab.id) || {}),
    ...state,
    updated: Date.now(),
  });
  forwardState(sender.tab.id);
});

browser.tabs.onRemoved.addListener((tabId) => {
  tabStates.delete(tabId);
  if (tabStates.size === 0) {
    // Last Meet tab closed; tell Megingjord to drop the Meet keys.
    sendToMegingjord({ event: "phase", phase: "none" });
    return;
  }
  // The best tab may have changed; forward the new best tab's state.
  const best = bestTabId();
  if (best !== null) {
    forwardState(best);
  }
});

browser.storage.local.get({ wsUrl: DEFAULT_WS_URL }).then((items) => {
  wsUrl = items.wsUrl || DEFAULT_WS_URL;
  connect();
});

browser.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.wsUrl) {
    wsUrl = changes.wsUrl.newValue || DEFAULT_WS_URL;
    if (socket) {
      socket.close();
    }
  }
});
