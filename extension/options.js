// Megingjord Google Meet extension - options page.
//
// Configures the WebSocket URL of the Megingjord app. The default is
// ws://127.0.0.1:2394.

const DEFAULT_WS_URL = "ws://127.0.0.1:2394";

const form = document.getElementById("options");
const input = document.getElementById("wsUrl");

browser.storage.local.get({ wsUrl: DEFAULT_WS_URL }).then((items) => {
  input.value = items.wsUrl || DEFAULT_WS_URL;
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  browser.storage.local.set({ wsUrl: input.value.trim() || DEFAULT_WS_URL });
});
