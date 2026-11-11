const host = window.chrome.webview;
const requests = new Map();
let messageId = 0;
let opened = false;
let epoch = 0;
let statusRequest = false;
let pairRequest = false;
let retryPairAt = 0;

const state = Object.assign(newState(), { running: null, devices: [], expiresAt: 0, pair: null, busy: false, revision: 0 });
states.c = state;
mountPanel(document.getElementById("native-panel"), "c");
wirePanelEvents();
updateMarks();

function callNative(operation, payload = {}) {
  const id = ++messageId;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      requests.delete(id);
      reject(new Error("操作超时，请重试"));
    }, operation === "settings.update" || operation === "settings.remove" ? 95000 : 12000);
    requests.set(id, { resolve, reject, timeout });
    host.postMessage({ id, operation, payload });
  });
}

function acceptDevices(model, reply) {
  if (!Array.isArray(reply.Devices)) return;
  const existing = new Set(model.devices.map(device => device.id));
  model.devices = reply.Devices.map(device => ({ id: String(device.Id), name: String(device.Name) }));
  if (model.devices.some(device => !existing.has(device.id)) && model.pair) {
    model.expiresAt = 0;
    retryPairAt = 0;
  }
}

function acceptStatus(model, reply) {
  model.running = Boolean(reply.Running);
  model.paused = Boolean(reply.Paused);
  acceptDevices(model, reply);
  if (!model.running) {
    model.pair = null;
    model.expiresAt = 0;
  }
}

async function changeNative(model, operation, payload) {
  if (model.busy) return null;
  const current = epoch;
  model.busy = true;
  model.revision++;
  try {
    const reply = await callNative(operation, payload);
    return current === epoch ? reply : null;
  } catch (error) {
    if (current === epoch && opened) notify("c", error.message, true);
    return null;
  } finally {
    model.busy = false;
  }
}

async function refreshNativePair(model, renew = false) {
  if (!opened || model.busy || !model.running || pairRequest) return;
  const now = Date.now();
  if (!renew && (now < model.expiresAt || now < retryPairAt)) return;
  pairRequest = true;
  const current = epoch;
  try {
    const reply = await callNative("pair", { renew });
    if (current !== epoch || !model.running) return;
    model.pair = { pin: reply.Pin, url: reply.Url, qr: "data:image/png;base64," + reply.Qr };
    model.expiresAt = reply.ExpiresAt * 1000;
    model.pairFailed = false;
  } catch (error) {
    if (current !== epoch) return;
    model.pairFailed = true;
    retryPairAt = Date.now() + 3000;
    if (renew) notify("c", error.message, true);
  } finally {
    pairRequest = false;
    if (current === epoch) render("c");
  }
}

async function pollNative() {
  if (!opened || state.busy || statusRequest) return;
  statusRequest = true;
  const current = epoch;
  const revision = state.revision;
  try {
    const reply = await callNative("status");
    if (current !== epoch || revision !== state.revision) return;
    acceptStatus(state, reply);
    render("c");
    void refreshNativePair(state);
  } catch (error) {
    if (current !== epoch || revision !== state.revision) return;
    if (state.running === null) state.running = false;
    notify("c", error.message, true);
  } finally {
    statusRequest = false;
  }
}

host.addEventListener("message", event => {
  const message = event.data;
  if (message.type === "visibility") {
    document.documentElement.dataset.theme = message.dark ? "dark" : "light";
    updateMarks();
    if (opened === message.visible) return;
    opened = message.visible;
    epoch++;
    if (opened) {
      const panel = document.querySelector(".panel");
      panel.classList.add("is-opening");
      setTimeout(() => panel.classList.remove("is-opening"), 160);
      void pollNative();
    } else {
      state.pair = null;
      state.expiresAt = 0;
      state.copySequence++;
      state.copied = "";
      state.uninstallConfirm = false;
      retryPairAt = 0;
      setScene("c", "home");
    }
    return;
  }
  const request = requests.get(message.id);
  if (!request) return;
  requests.delete(message.id);
  clearTimeout(request.timeout);
  if (message.reply.Error) request.reject(new Error(message.reply.Error));
  else request.resolve(message.reply);
});

setInterval(pollNative, 1200);
setInterval(() => {
  if (!opened) return;
  paintPairing(document.querySelector(".panel"), state);
  if (Date.now() >= state.expiresAt) void refreshNativePair(state);
}, 250);
host.postMessage({ operation: "ready" });
