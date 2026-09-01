// Megingjord Google Meet extension - content script.
//
// Runs in Google Meet tabs. Detects the meeting phase (lobby, green room,
// meeting, exit hall) and the mic/camera/hand states, and executes commands
// by clicking the corresponding Meet UI buttons.
//
// State is reported to the background script on change, on a low-frequency
// poll (background tabs have throttled timers, so this is a fallback for
// missed mutations), and when the tab becomes visible again.
//
// Selectors were verified against the live Meet UI (September 2026).

// Phase detection. The meeting check comes first: the exit hall heading
// jsname also appears in the green room (as the meeting title), so the exit
// hall check requires the absence of mute controls.
function detectPhase() {
  const path = window.location.pathname;
  if (path === "/" || path === "/home" || path === "/landing") {
    return "lobby";
  }
  if (document.querySelector('[jsname="CQylAd"]')) {
    return "meeting";
  }
  if (document.querySelector('[jsname="Qx7uuf"]')) {
    return "greenRoom";
  }
  if (
    document.querySelector('[jsname="r4nke"]') &&
    !document.querySelector("[data-is-muted]")
  ) {
    return "exitHall";
  }
  return undefined;
}

const MIC_SELECTORS = [
  'button[jsname="hw0c9"]', // verified: meeting and green room
  'div[role="button"][jsname="hw0c9"]', // older Join screen
  'div[jsname="Dg9Wp"] [jsname="BOHaEe"]', // pre-2024 redesign
];

const CAMERA_SELECTORS = [
  'button[jsname="psRWwc"]', // verified: meeting and green room
  'div[role="button"][jsname="psRWwc"]', // older Join screen
  'div[jsname="R3GXJb"] [jsname="BOHaEe"]', // pre-2024 redesign
];

const HAND_SELECTORS = ['button[jsname="FpSaz"]']; // verified: meeting

const LEAVE_SELECTOR = '[jsname="CQylAd"]'; // verified: meeting
const LEAVE_CONFIRMATION_SELECTOR = '[data-mdc-dialog-action="Pd96ce"]';

const START_INSTANT_SELECTOR = '[jsname="CuSyi"]'; // verified: lobby
const SCHEDULED_SECTION_SELECTOR = 'SECTION[jsname="n39Uf"]'; // verified: lobby
const ENTER_MEETING_SELECTOR = '[jsname="Qx7uuf"]'; // verified: green room
const ENTER_MEETING_HOST_SELECTOR = '[jsname="z0F4cd"]'; // verified: green room (host)
const REJOIN_SELECTOR = 'button[jsname="W6suGc"]'; // verified: exit hall, inner button
const RETURN_HOME_SELECTOR = '[jsname="WIVZEd"] button'; // verified: exit hall
const RETURN_HOME_GREEN_ROOM_SELECTOR =
  '[aria-label="Return to home screen"]'; // verified: green room

function firstMatch(selectors) {
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    if (element) {
      return element;
    }
  }
  return null;
}

// The join button is a DIV wrapper around the actual button element; the
// disabled state lives on the inner button.
function getJoinButton() {
  const outer = firstMatch([
    ENTER_MEETING_SELECTOR,
    ENTER_MEETING_HOST_SELECTOR,
  ]);
  if (!outer) {
    return null;
  }
  return outer.querySelector("button") || outer;
}

function isDisabled(element) {
  return (
    element.disabled || element.getAttribute("aria-disabled") === "true"
  );
}

// The first meeting card in the Scheduled section.
function firstScheduledCard() {
  const section = document.querySelector(SCHEDULED_SECTION_SELECTOR);
  if (!section) {
    return null;
  }
  return section.querySelector('[jsname="PoaP2b"]');
}

// The hand button does not use aria-pressed; raised is indicated by a CSS
// class and the aria-label flipping to "Lower hand".
function isHandRaised(button) {
  const cls = (button.className || "").toString();
  if (cls.includes("HlOR8e")) {
    return true;
  }
  const label = (button.getAttribute("aria-label") || "").toLowerCase();
  return label.includes("lower hand");
}

function readState() {
  const state = { phase: detectPhase() };

  const micButton = firstMatch(MIC_SELECTORS);
  if (micButton) {
    state.micMuted = micButton.dataset.isMuted === "true";
  }

  const cameraButton = firstMatch(CAMERA_SELECTORS);
  if (cameraButton) {
    state.cameraMuted = cameraButton.dataset.isMuted === "true";
  }

  const handButton = firstMatch(HAND_SELECTORS);
  if (handButton) {
    state.handMuted = !isHandRaised(handButton);
  }

  if (state.phase === "greenRoom") {
    const enterButton = getJoinButton();
    if (enterButton) {
      state.enterReady = !isDisabled(enterButton);
      state.enterLabel = (enterButton.textContent || "").trim();
    }
    const titleElement = document.querySelector('[jsname="r4nke"]');
    if (titleElement) {
      state.meetingTitle = (titleElement.textContent || "").trim();
    }
  } else if (state.phase === "lobby") {
    const card = firstScheduledCard();
    state.hasNextMeeting = !!card;
    if (card) {
      state.nextMeetingTitle = (
        card.getAttribute("aria-label") || card.textContent || ""
      ).trim();
    }
  }

  return state;
}

function clickButton(selector, name) {
  const element = document.querySelector(selector);
  if (element) {
    element.click();
    return true;
  }
  console.warn(`Megingjord Meet: button not found: ${name}`);
  return false;
}

function clickByText(text) {
  const element = queryByText(text);
  if (element) {
    element.click();
    return true;
  }
  return false;
}

function queryByText(text) {
  for (const element of document.querySelectorAll("button,[role=button]")) {
    if ((element.textContent || "").trim() === text) {
      return element;
    }
  }
  return null;
}

const COMMANDS = {
  toggleMic: () => clickButton(MIC_SELECTORS.join(","), "mic"),
  toggleCamera: () => clickButton(CAMERA_SELECTORS.join(","), "camera"),
  toggleHand: () => clickButton(HAND_SELECTORS.join(","), "hand"),
  leaveCall: () => {
    // Some meetings ask to confirm leaving; a second press selects
    // "just leave the call".
    if (!clickButton(LEAVE_CONFIRMATION_SELECTOR, "leave confirmation")) {
      clickButton(LEAVE_SELECTOR, "leave");
    }
  },
  startInstantMeeting: () =>
    clickButton(START_INSTANT_SELECTOR, "start instant meeting") ||
    clickButton('[aria-label="New meeting"]', "new meeting"),
  startNextMeeting: () => {
    const card = firstScheduledCard();
    if (card) {
      card.click();
      return;
    }
    console.warn("Megingjord Meet: button not found: start next meeting");
  },
  enterMeeting: () => {
    const button = getJoinButton();
    if (!button) {
      console.warn("Megingjord Meet: button not found: join now");
      return;
    }
    if (isDisabled(button)) {
      console.warn("Megingjord Meet: join button not ready");
      return;
    }
    button.click();
  },
  rejoin: () => clickButton(REJOIN_SELECTOR, "rejoin") || clickByText("Rejoin"),
  returnHome: () =>
    clickButton(RETURN_HOME_SELECTOR, "return home") ||
    clickButton(RETURN_HOME_GREEN_ROOM_SELECTOR, "return home (green room)") ||
    clickByText("Return to home screen"),
};

let lastState = null;

function sendState(force = false) {
  const state = readState();
  if (force || JSON.stringify(state) !== JSON.stringify(lastState)) {
    lastState = state;
    browser.runtime.sendMessage({ type: "state", state });
  }
}

// Watch for phase changes (childList) and mute state changes (attributes).
const observer = new MutationObserver(sendState);
observer.observe(document.body, {
  childList: true,
  attributes: true,
  attributeFilter: ["data-is-muted", "aria-pressed"],
  subtree: true,
});

// Fallback for changes missed while the tab was throttled in the background.
setInterval(sendState, 1000);

// Keep the background event page alive (it is unloaded after ~30s of
// inactivity, which would kill the WebSocket) and re-push state after
// Megingjord restarts.
setInterval(() => sendState(true), 20000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    sendState();
  }
});

browser.runtime.onMessage.addListener((message) => {
  if (message.type === "getState") {
    sendState();
  } else if (message.type === "command") {
    const handler = COMMANDS[message.event];
    if (handler) {
      handler();
      // Re-read state shortly after the click; Meet updates the DOM
      // asynchronously.
      setTimeout(sendState, 100);
    }
  }
});
