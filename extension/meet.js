// Megingjord Google Meet extension - content script.
//
// Runs in Google Meet tabs. Detects the meeting phase (lobby, green room,
// meeting, exit hall) and the mic/camera/hand states, and executes commands
// by clicking the corresponding Meet UI buttons.
//
// State is reported to the background script on change, on a low-frequency
// poll (background tabs have throttled timers, so this is a fallback for
// missed mutations), and when the tab becomes visible again.

// Phase detection. The meeting check comes first: some selectors of other
// phases may also be present while in a meeting.
const PHASE_SELECTORS = [
  { phase: "meeting", selector: "div[data-meeting-title]" },
  { phase: "greenRoom", selector: "[jscontroller=dyDNGc]" },
  { phase: "exitHall", selector: "[jsname=r4nke]" },
];

const MIC_SELECTORS = [
  'button[jsname="hw0c9"]', // after September 2024 Meet redesign
  'div[role="button"][jsname="hw0c9"]', // used on the Join screen
  'div[jsname="Dg9Wp"] [jsname="BOHaEe"]', // before September 2024 redesign
];

const CAMERA_SELECTORS = [
  'button[jsname="psRWwc"]', // after September 2024 Meet redesign
  'div[role="button"][jsname="psRWwc"]', // used on the Join screen
  'div[jsname="R3GXJb"] [jsname="BOHaEe"]', // before September 2024 redesign
];

const HAND_SELECTORS = ['button[jsname="FpSaz"]'];

const LEAVE_SELECTOR = '[jsname="CQylAd"]';
const LEAVE_CONFIRMATION_SELECTOR = '[data-mdc-dialog-action="Pd96ce"]';

const START_INSTANT_SELECTOR = '[jsname="CuSyi"]';
const START_NEXT_SELECTOR = '[data-default-focus=true]';
const ENTER_MEETING_SELECTOR = '[jsname="Qx7uuf"]';
const REJOIN_SELECTOR = '[jsname="oI7Fj"] button';
const RETURN_HOME_SELECTOR = '[jsname="WIVZEd"] button';

function detectPhase() {
  const path = window.location.pathname;
  if (path === "/" || path === "/landing") {
    return "lobby";
  }
  for (const { phase, selector } of PHASE_SELECTORS) {
    if (document.querySelector(selector)) {
      return phase;
    }
  }
  return undefined;
}

function firstMatch(selectors) {
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    if (element) {
      return element;
    }
  }
  return null;
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
    // "muted" means the hand is not raised.
    state.handMuted = handButton.getAttribute("aria-pressed") !== "true";
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
    clickButton(START_INSTANT_SELECTOR, "start instant meeting"),
  startNextMeeting: () =>
    clickButton(START_NEXT_SELECTOR, "start next meeting"),
  enterMeeting: () => clickButton(ENTER_MEETING_SELECTOR, "join now"),
  rejoin: () => clickButton(REJOIN_SELECTOR, "rejoin"),
  returnHome: () => clickButton(RETURN_HOME_SELECTOR, "return home"),
};

let lastState = null;

function sendState() {
  const state = readState();
  if (JSON.stringify(state) !== JSON.stringify(lastState)) {
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
