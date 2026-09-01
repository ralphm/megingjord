// Megingjord Google Meet extension - background script.
//
// Owns the WebSocket connection to Megingjord (ws://127.0.0.1:2394) and
// aggregates state from all Google Meet tabs. Keeping the socket in the
// background script means it is not throttled when a tab is in the
// background, and it is not subject to page-level ad blockers or the
// browser's localhost permission prompt.

const WS_URL = "ws://127.0.0.1:2394";
const RECONNECT_INTERVAL_MS = 2000;

// Phases with higher priority win when multiple Meet tabs are open.
const PHASE_PRIORITY = {
  meeting: 3,
  greenRoom: 2,
  lobby: 1,
  exitHall: 0,
};

let socket = null;
let reconnectTimer = null;

// tabId -> {phase, micMuted, cameraMuted, handMuted, updated}
const tabStates = new Map();

function connect() {
  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    console.log("Megingjord Meet: connected to Megingjord");
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

// Forward the state of the given tab if it is the best tab.
function forwardState(tabId) {
  if (tabId !== bestTabId()) {
    return;
  }
  const state = tabStates.get(tabId);
  if (!state) {
    return;
  }
  if (state.phase !== undefined) {
    sendToMegingjord({ event: "phase", phase: state.phase });
  }
  if (state.micMuted !== undefined) {
    sendToMegingjord({ event: "micMutedState", muted: state.micMuted });
  }
  if (state.cameraMuted !== undefined) {
    sendToMegingjord({ event: "cameraMutedState", muted: state.cameraMuted });
  }
  if (state.handMuted !== undefined) {
    sendToMegingjord({ event: "handMutedState", muted: state.handMuted });
  }
}

browser.runtime.onMessage.addListener((message, sender) => {
  if (!sender.tab || message.type !== "state") {
    return;
  }
  tabStates.set(sender.tab.id, { ...message.state, updated: Date.now() });
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

connect();
