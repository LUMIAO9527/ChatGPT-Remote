const assets = __REMOTE_ASSET_DATA__;
const nativePanel = Boolean(window.chrome?.webview && document.getElementById("native-panel"));
const layouts = { c: { name: "并排连接" } };
const states = {};
const dialog = document.getElementById("context");
const contextApp = document.getElementById("context-app");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const pairLifetime = 60;
let scenario = "normal";

function icon(name) {
  return '<span class="icon" aria-hidden="true">' + assets.icons[name] + "</span>";
}
function button(action, label, content, className, extra = "") {
  return '<button type="button" class="' + className + '" data-action="' + action + '" aria-label="' + label + '" title="'
    + label + '" ' + extra + ">" + content + "</button>";
}
function mark() {
  return document.documentElement.dataset.theme === "light" ? assets.markLight : assets.mark;
}
function updateMarks() {
  document.querySelectorAll("[data-brand]").forEach(element => { element.src = mark(); });
}
function newState() {
  const counts = { empty: 0, single: 1, normal: 2, three: 3, many: 8 };
  const count = counts[scenario] ?? 2;
  return {
    devices: Array.from({ length: count }, (_, index) => ({
      id: String(index + 1),
      name: index === 0 ? (scenario === "long" ? "用于阅读消息的主力手机 Mate 90 Pro" : "我的手机")
        : index === 1 ? "备用手机" : "手机 " + (index + 1)
    })),
    running: true, settings: false, paused: scenario === "paused",
    phase: "", activeId: 0, draft: "", pairIndex: 0,
    uninstallConfirm: false, uninstallTimer: null, uninstalled: false,
    expiresAt: Date.now() + (scenario === "expired" ? 0 : pairLifetime * 1000),
    update: "idle", notice: "", error: false, noticeTimer: null,
    copied: "", copyFailed: false, copyTimer: null, copySequence: 0
  };
}
function ring() {
  return button("refresh", "刷新二维码",
    '<svg class="ring-meter" viewBox="0 0 22 22" aria-hidden="true"><circle class="ring-track" cx="11" cy="11" r="9"/>'
    + '<circle class="ring-arc" cx="11" cy="11" r="9" pathLength="100" transform="rotate(-90 11 11)"/></svg>',
    "ring-button");
}
function qr() {
  return '<div class="qr-box"><img alt="配对二维码" draggable="false"><span hidden>已过期</span></div>';
}
function codeTools() {
  return '<div class="code-tools">'
    + button("copy-pin", "复制配对码", '<span class="pin"></span>', "code-copy")
    + ring() + "</div>";
}
function copyLink() {
  return button("copy", "复制链接", icon("copy") + '<span class="copy-url"></span>', "panel-action copy");
}
function deviceArea() {
  return '<div class="device-area"><div class="card-window"><div class="device-track" tabindex="0" aria-label="已配对设备"></div>'
    + '<span class="empty" hidden>' + icon("smartphone") + '<span data-empty-label>暂无设备</span></span></div>'
    + '<div class="device-foot">' + copyLink() + '<span class="device-total" hidden></span></div></div>';
}
function settings() {
  return '<div class="settings" aria-label="插件设置" hidden><div class="settings-version"><strong>Remote</strong><span>'
    + assets.version + '</span></div>'
    + button("update", "检查更新", icon("refresh-cw") + '<span data-update-label>检查更新</span>', "panel-action")
    + button("uninstall", "卸载插件", icon("trash-2") + "<span data-uninstall-label>卸载插件</span>", "panel-action danger") + "</div>";
}
function panelMarkup(id) {
  return '<section class="panel" data-panel="' + id + '" aria-label="手机连接">'
    + '<header class="panel-head"><h3 class="panel-title panel-brand"><img data-brand alt=""><span>ChatGPT Remote</span></h3>'
    + button("pause", "暂停手机连接", '<span class="status-dot"></span><span data-status-label></span>', "status")
    + button("settings", "设置", icon("settings"), "panel-tool", 'aria-expanded="false"')
    + '</header><div class="panel-main">' + qr() + codeTools() + '<div class="right-area">' + deviceArea() + "</div>"
    + '</div>' + settings() + '<div class="notice" role="status" hidden></div>'
    + '<span class="copy-result sr-only" role="status"></span></section>';
}
function newCard(id) {
  const card = document.createElement("div");
  card.className = "device-card";
  card.dataset.deviceId = id;
  card.innerHTML = '<span class="device-glyph">' + icon("smartphone") + "</span>"
    + button("rename", "重命名设备", "", "card-name")
    + '<textarea class="card-name card-input" aria-label="设备名称" rows="2" maxlength="60" spellcheck="false" hidden></textarea>'
    + button("remove", "删除设备", icon("trash-2"), "card-delete")
    + '<div class="card-controls edit-controls" hidden>'
    + button("cancel-edit", "取消重命名", icon("x"), "card-command")
    + button("save-name", "保存名称", icon("check"), "card-command apply") + "</div>";
  return card;
}
function updateCard(card, device, state) {
  const phase = state.activeId === device.id ? state.phase : "";
  const editing = phase === "edit";
  const removing = phase === "remove";
  card.dataset.phase = phase;
  const name = card.querySelector("button.card-name");
  if (name.textContent !== device.name) name.textContent = device.name;
  name.title = device.name;
  name.setAttribute("aria-label", "重命名 " + device.name);
  name.hidden = editing;
  const input = card.querySelector(".card-input");
  input.hidden = !editing;
  if (editing && input.value !== state.draft) input.value = state.draft;
  card.querySelector(".device-glyph").hidden = editing || removing;
  const deleteButton = card.querySelector(".card-delete");
  deleteButton.hidden = editing;
  deleteButton.setAttribute("aria-label", removing ? "再次点击删除 " + device.name : "删除 " + device.name);
  deleteButton.title = removing ? "再次点击删除" : "删除设备";
  card.querySelector(".edit-controls").hidden = !editing;
}
function updateFades(track) {
  const width = 8;
  const left = Math.max(0, track.scrollLeft);
  const right = Math.max(0, track.scrollWidth - track.clientWidth - left);
  track.dataset.left = String(left > 1);
  track.dataset.right = String(right > 1);
  track.style.setProperty("--fade-left", Math.min(left, width) + "px");
  track.style.setProperty("--fade-right", Math.min(right, width) + "px");
}
function updateCards(panel, state, animate) {
  const track = panel.querySelector(".device-track");
  const cards = new Map(Array.from(track.children, card => [card.dataset.deviceId, card]));
  const oldPositions = animate && !reducedMotion.matches
    ? new Map(Array.from(cards, ([id, card]) => [id, card.getBoundingClientRect().left])) : null;
  const scroll = track.scrollLeft;
  for (const [index, device] of state.devices.entries()) {
    const key = String(device.id);
    const existing = cards.get(key);
    const card = existing || newCard(device.id);
    if (!existing) {
      card.dataset.enter = "true";
      window.setTimeout(() => { delete card.dataset.enter; }, 220);
    }
    cards.delete(key);
    if (track.children[index] !== card) track.insertBefore(card, track.children[index] || null);
    updateCard(card, device, state);
  }
  for (const card of cards.values()) card.remove();
  if (track.scrollLeft !== scroll) track.scrollLeft = scroll;
  panel.querySelector(".empty").hidden = state.devices.length > 0;
  panel.querySelector("[data-empty-label]").textContent = state.running === null ? "连接中…" : "暂无设备";
  track.hidden = !state.devices.length;
  track.dataset.count = String(state.devices.length);
  updateFades(track);
  if (!oldPositions) return;
  const moves = Array.from(track.children, card => {
    const previous = oldPositions.get(card.dataset.deviceId);
    return { card, distance: previous - card.getBoundingClientRect().left };
  });
  for (const { card, distance } of moves) {
    if (!distance || !Number.isFinite(distance)) continue;
    card.getAnimations().forEach(animation => animation.cancel());
    card.animate([{ transform: "translateX(" + distance + "px)" }, { transform: "translateX(0)" }],
      { duration: 165, easing: "cubic-bezier(.2,.7,.2,1)" });
  }
}
function setupTrack(track) {
  let drag = null;
  let skipClick = false;
  let frame = 0;
  let target = 0;
  let wheelEnd;
  let previousTime = 0;
  const maximum = () => Math.max(0, track.scrollWidth - track.clientWidth);
  const clamp = position => Math.min(maximum(), Math.max(0, position));
  const stride = () => track.firstElementChild
    ? track.firstElementChild.getBoundingClientRect().width + parseFloat(getComputedStyle(track).columnGap) : 1;
  const snapped = position => clamp(Math.round(position / stride()) * stride());

  function stopMotion() {
    cancelAnimationFrame(frame);
    frame = 0;
    previousTime = 0;
  }
  function advance(time) {
    target = clamp(target);
    const offset = target - track.scrollLeft;
    if (Math.abs(offset) < 1) {
      track.scrollLeft = target;
      stopMotion();
      return;
    }
    const delta = previousTime ? Math.min(40, time - previousTime) : 16;
    previousTime = time;
    const step = offset * (1 - Math.exp(-delta / 52));
    track.scrollLeft += Math.sign(step) * Math.max(1, Math.abs(step));
    frame = requestAnimationFrame(advance);
  }
  function scrollTo(position) {
    target = clamp(position);
    if (reducedMotion.matches) {
      stopMotion();
      track.scrollLeft = target;
    } else if (!frame) {
      frame = requestAnimationFrame(advance);
    }
  }
  track.addEventListener("scroll", () => updateFades(track), { passive: true });
  track.addEventListener("wheel", event => {
    if (event.ctrlKey || !maximum()) return;
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    const start = frame ? target : track.scrollLeft;
    if (!delta || clamp(start + delta) === start) return;
    event.preventDefault();
    const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? track.clientWidth : 1;
    const movement = delta * scale;
    scrollTo(start + movement);
    clearTimeout(wheelEnd);
    wheelEnd = setTimeout(() => {
      let next = snapped(target);
      if (Math.abs(movement) >= 35 && Math.abs(next - start) < 2) {
        next = snapped(start + Math.sign(movement) * stride());
      }
      scrollTo(next);
    }, 110);
  }, { passive: false });
  track.addEventListener("keydown", event => {
    if (event.target !== track || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || !maximum()) return;
    event.preventDefault();
    clearTimeout(wheelEnd);
    const position = frame ? target : track.scrollLeft;
    if (event.key === "Home") scrollTo(0);
    else if (event.key === "End") scrollTo(maximum());
    else scrollTo(snapped(position + (event.key === "ArrowLeft" ? -stride() : stride())));
  });
  track.addEventListener("pointerdown", event => {
    if (event.pointerType !== "mouse" || event.button !== 0 || event.target.closest(".card-input") || !maximum()) return;
    clearTimeout(wheelEnd);
    stopMotion();
    skipClick = false;
    drag = { id: event.pointerId, x: event.clientX, scroll: track.scrollLeft, moved: false,
      lastX: event.clientX, lastTime: event.timeStamp, speed: 0 };
  });
  track.addEventListener("pointermove", event => {
    if (!drag || drag.id !== event.pointerId || !event.buttons) return;
    const distance = drag.x - event.clientX;
    if (!drag.moved && Math.abs(distance) < 5) return;
    if (!drag.moved) {
      drag.moved = true;
      track.setPointerCapture(event.pointerId);
      track.dataset.dragging = "true";
    }
    track.scrollLeft = drag.scroll + distance;
    const elapsed = event.timeStamp - drag.lastTime;
    if (elapsed > 0) drag.speed = (drag.lastX - event.clientX) / elapsed;
    drag.lastX = event.clientX;
    drag.lastTime = event.timeStamp;
  });
  function endDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    skipClick = drag.moved;
    if (drag.moved) {
      const momentum = event.type === "pointerup" && event.timeStamp - drag.lastTime < 100
        ? Math.max(-stride(), Math.min(stride(), drag.speed * 100)) : 0;
      scrollTo(snapped(track.scrollLeft + (reducedMotion.matches ? 0 : momentum)));
    }
    drag = null;
    delete track.dataset.dragging;
    window.setTimeout(() => { skipClick = false; }, 0);
  }
  track.addEventListener("pointerup", endDrag);
  track.addEventListener("pointercancel", endDrag);
  track.addEventListener("lostpointercapture", endDrag);
  track.addEventListener("click", event => {
    if (!skipClick) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);
}
function pairing(state) {
  return nativePanel ? state.pair : assets.pairs[state.pairIndex % assets.pairs.length];
}
function linkLabel(code) {
  if (!code) return "复制链接";
  const url = new URL(code.url);
  return url.host + (url.pathname === "/" ? "" : url.pathname);
}
function paintPairing(panel, state, refreshImage = false) {
  const code = pairing(state);
  const seconds = Math.max(0, Math.ceil((state.expiresAt - Date.now()) / 1000));
  const expired = seconds === 0;
  const ringButton = panel.querySelector(".ring-button");
  const ringLabel = expired ? "配对码已过期，点击刷新" : "刷新二维码，剩余 " + seconds + " 秒";
  if (ringButton.title !== ringLabel) {
    ringButton.title = ringLabel;
    ringButton.setAttribute("aria-label", ringLabel);
  }
  ringButton.dataset.expired = String(expired);
  ringButton.disabled = state.running !== true;
  ringButton.querySelector(".ring-arc").style.strokeDashoffset = String(100 - Math.min(seconds, pairLifetime) * 100 / pairLifetime);
  const qrBox = panel.querySelector(".qr-box");
  if (refreshImage || qrBox.dataset.expired !== String(expired)) {
    qrBox.dataset.expired = String(expired);
    const image = qrBox.querySelector("img");
    image.hidden = !code;
    if (code && image.getAttribute("src") !== code.qr) {
      image.src = code.qr;
      image.getAnimations().forEach(animation => animation.cancel());
      if (!reducedMotion.matches) image.animate([{ opacity: .45 }, { opacity: 1 }], { duration: 150 });
    }
    if (!code) image.removeAttribute("src");
    const placeholder = qrBox.querySelector("span");
    placeholder.hidden = !expired && Boolean(code);
    placeholder.textContent = !code ? (state.running === false ? "连接未启动" : state.pairFailed ? "点击刷新" : "加载中…") : "已过期";
    const pin = panel.querySelector(".pin");
    if (state.copied !== "pin") pin.textContent = !code ? "······" : expired ? "已过期" : code.pin;
    pin.dataset.expired = String(expired);
    panel.querySelectorAll('[data-action="copy"],[data-action="copy-pin"]').forEach(control => { control.disabled = expired || !code; });
  }
}
function paintCopyFeedback(panel, state) {
  const link = panel.querySelector('[data-action="copy"]');
  const linkFeedback = state.copied === "link";
  const iconName = linkFeedback ? (state.copyFailed ? "circle-alert" : "check") : "copy";
  const current = link.querySelector(".icon");
  if (current && current.dataset.name !== iconName) {
    current.outerHTML = icon(iconName);
    link.querySelector(".icon").dataset.name = iconName;
  }
  link.dataset.error = String(linkFeedback && state.copyFailed);
  link.title = linkFeedback ? (state.copyFailed ? "复制失败" : "链接已复制") : "复制链接";
  const urlLabel = link.querySelector(".copy-url");
  if (urlLabel) {
    urlLabel.textContent = linkFeedback
      ? (state.copyFailed ? "复制失败" : "已复制链接")
      : linkLabel(pairing(state));
  }

  const code = panel.querySelector(".code-copy");
  const pinFeedback = state.copied === "pin";
  code.dataset.error = String(pinFeedback && state.copyFailed);
  code.dataset.copied = String(pinFeedback && !state.copyFailed);
  code.title = pinFeedback ? (state.copyFailed ? "复制失败" : "配对码已复制") : "复制配对码";
  const pin = panel.querySelector(".pin");
  if (pinFeedback) {
    pin.dataset.feedback = "true";
    pin.textContent = state.copyFailed ? "复制失败" : "已复制";
  } else if (pin.dataset.feedback) {
    delete pin.dataset.feedback;
  }

  panel.querySelector(".copy-result").textContent = state.copied
    ? (state.copyFailed ? "复制失败" : state.copied === "pin" ? "配对码已复制" : "链接已复制") : "";
}
function updatePanel(panel, state, animate = false) {
  panel.querySelector(".card-window").inert = state.settings;
  panel.querySelector(".settings").hidden = !state.settings;
  panel.querySelector("[data-action=settings]").setAttribute("aria-expanded", String(state.settings));
  const status = panel.querySelector(".status");
  status.setAttribute("aria-pressed", String(Boolean(state.running) && !state.paused));
  status.setAttribute("aria-label", state.running === false ? "启动手机连接" : state.paused ? "恢复手机连接" : "暂停手机连接");
  status.title = status.getAttribute("aria-label");
  status.disabled = state.running === null;
  status.dataset.paused = String(state.paused);
  status.querySelector("[data-status-label]").textContent = state.running === null ? "连接中…" :
    !state.running ? "未连接" : state.paused ? "已暂停" : "已开启";
  const updateLabel = { idle: "检查更新", busy: "检查中…", ready: "安装更新", latest: "已是最新", installed: "已安装", failed: "检查失败" }[state.update];
  panel.querySelector("[data-update-label]").textContent = updateLabel;
  const update = panel.querySelector("[data-action=update]");
  update.disabled = state.update === "busy";
  update.setAttribute("aria-label", updateLabel);
  update.title = updateLabel;
  const uninstall = panel.querySelector("[data-action=uninstall]");
  const uninstallLabel = state.uninstalled ? "已卸载" : state.uninstallConfirm ? "再点一次卸载" : "卸载插件";
  uninstall.dataset.confirm = String(state.uninstallConfirm);
  uninstall.disabled = state.uninstalled;
  uninstall.setAttribute("aria-label", uninstallLabel);
  uninstall.title = uninstallLabel;
  uninstall.querySelector("[data-uninstall-label]").textContent = uninstallLabel;
  updateCards(panel, state, animate);
  const total = panel.querySelector(".device-total");
  if (total) {
    total.hidden = state.devices.length <= 2;
    total.textContent = "共 " + state.devices.length + " 台";
  }
  paintPairing(panel, state, true);
  paintCopyFeedback(panel, state);
  const notice = panel.querySelector(".notice");
  notice.hidden = !state.notice;
  notice.dataset.error = String(state.error);
  if (state.notice) {
    notice.innerHTML = icon(state.error ? "circle-alert" : "check") + "<span></span>";
    notice.lastElementChild.textContent = state.notice;
  }
}
function render(id, animate = false) {
  document.querySelectorAll('[data-panel="' + id + '"]').forEach(panel => updatePanel(panel, states[id], animate));
}
function mountPanel(mount, id) {
  mount.innerHTML = panelMarkup(id);
  const panel = mount.firstElementChild;
  setupTrack(panel.querySelector(".device-track"));
  updatePanel(panel, states[id]);
}
function rail() {
  return '<div class="rail" aria-hidden="true">' + icon("house") + icon("panels-top-left") + icon("history")
    + '<div class="rail-bottom"><div class="rail-item"><img data-brand alt=""></div><div class="rail-item">'
    + icon("circle-gauge") + "</div></div></div>";
}
function showContext(label, content) {
  document.getElementById("context-label").textContent = label;
  document.getElementById("replay-mobile").hidden = true;
  contextApp.innerHTML = content;
  if (!dialog.open) dialog.showModal();
  updateMarks();
}
function showDesktop(id) {
  const layout = layouts[id];
  showContext(layout.name, rail()
    + '<aside class="context-sidebar"><strong>ChatGPT</strong><div>新聊天</div><div>AppLab</div><div>ChatGPT Remote</div></aside>'
    + '<div class="context-main"><h3>今天想做什么？</h3><div class="composer">使用 ChatGPT Work</div></div><div class="mount"></div>');
  mountPanel(contextApp.querySelector(".mount"), id);
}
function showMobile() {
  showContext("手机启动", '<iframe id="mobile-frame" class="mobile-preview" title="手机启动与对话" sandbox="allow-scripts allow-same-origin allow-forms"></iframe>');
  document.getElementById("replay-mobile").hidden = false;
  const config = JSON.stringify({ scenario: "chat", theme: document.documentElement.dataset.theme, version: assets.version });
  const delay = 'const originalFetch=window.fetch;window.fetch=async function(path,options){if(new URL(String(path),"https://preview.invalid").pathname==="/api/conversation/stream")await new Promise(resolve=>setTimeout(resolve,1600));return originalFetch(path,options);};';
  const script = "<script>window.RM_REVIEW_CONFIG=" + config + ";" + assets.mobileFixture + delay + "</" + "script>";
  document.getElementById("mobile-frame").srcdoc = assets.mobileHtml.replace("<head>", "<head>" + script);
}
function notify(id, text, error = false) {
  const state = states[id];
  state.notice = text;
  state.error = error;
  render(id);
  window.clearTimeout(state.noticeTimer);
  state.noticeTimer = window.setTimeout(() => { state.notice = ""; render(id); }, 1600);
}
function clearDeviceAction(state) {
  state.phase = "";
  state.activeId = 0;
  state.draft = "";
}
function setScene(id, scene) {
  const state = states[id];
  clearDeviceAction(state);
  state.notice = "";
  window.clearTimeout(state.noticeTimer);
  state.settings = scene === "settings";
  const first = state.devices[0];
  if (first && ["rename", "remove"].includes(scene)) {
    state.activeId = first.id;
    state.phase = scene === "rename" ? "edit" : scene;
    state.draft = first.name;
  }
  render(id);
}
function focusCard(panel, id, selector) {
  const card = panel.querySelector('[data-device-id="' + CSS.escape(id) + '"]');
  const control = card?.querySelector(selector);
  if (control) control.focus({ preventScroll: true });
  return control;
}
function editDevice(panel, id) {
  const scheme = panel.dataset.panel;
  const state = states[scheme];
  const device = state.devices.find(item => item.id === id);
  if (!device) return;
  state.settings = false;
  state.phase = "edit";
  state.activeId = id;
  state.draft = device.name;
  render(scheme);
  const input = focusCard(panel, id, ".card-input");
  if (input) { input.select(); input.scrollLeft = 0; }
}
async function saveName(panel) {
  const id = panel.dataset.panel;
  const state = states[id];
  const device = state.devices.find(item => item.id === state.activeId);
  if (!device) return;
  const name = state.draft.trim();
  if (!name) { notify(id, "请输入设备名称", true); return; }
  if (nativePanel) {
    const reply = await changeNative(state, "device.rename", { id: device.id, name });
    if (!reply) return;
    acceptDevices(state, reply);
  } else {
    if (scenario === "failure") { notify(id, "保存失败", true); return; }
    device.name = name;
  }
  clearDeviceAction(state);
  render(id);
  focusCard(panel, device.id, "button.card-name");
}
async function checkUpdate(id) {
  const state = states[id];
  if (state.update === "busy") return;
  if (nativePanel) {
    const installing = state.update === "ready";
    state.update = "busy";
    render(id);
    const reply = await changeNative(state, installing ? "settings.update" : "settings.check", { version: state.updateVersion });
    state.updateVersion = reply?.UpdateVersion;
    state.update = !reply ? "failed" : state.updateVersion ? "ready" :
      installing || reply.Message?.startsWith("已安装") ? "installed" : "latest";
    render(id);
    return;
  }
  if (state.update === "ready") { state.update = "installed"; render(id); return; }
  state.update = "busy";
  render(id);
  window.setTimeout(() => {
    if (states[id] !== state) return;
    state.update = scenario === "failure" ? "failed" : scenario === "update" ? "ready" : "latest";
    render(id);
  }, 450);
}
async function copyPairing(id, kind) {
  const state = states[id];
  if (Date.now() >= state.expiresAt) return;
  const sequence = ++state.copySequence;
  const code = pairing(state);
  if (!code) return;
  const payload = kind === "pin" ? code.pin : code.url;
  let failed = false;
  try {
    if (nativePanel) await callNative(kind === "pin" ? "clipboard.pin" : "clipboard.link");
    else await navigator.clipboard.writeText(payload);
  }
  catch { failed = true; }
  if (states[id] !== state || state.copySequence !== sequence) return;
  state.copied = kind;
  state.copyFailed = failed;
  render(id);
  window.clearTimeout(state.copyTimer);
  state.copyTimer = window.setTimeout(() => {
    if (states[id] !== state) return;
    state.copied = "";
    state.copyFailed = false;
    render(id);
  }, 1400);
}
async function handleAction(panel, control) {
  const id = panel.dataset.panel;
  const state = states[id];
  if (state.busy) return;
  const deviceId = control.closest(".device-card")?.dataset.deviceId || state.activeId;
  switch (control.dataset.action) {
    case "settings": {
      state.settings = !state.settings;
      clearDeviceAction(state);
      render(id);
      if (state.settings) panel.querySelector(".settings [data-action=update]").focus({ preventScroll: true });
      return;
    }
    case "pause":
      if (nativePanel) {
        const reply = await changeNative(state, state.running ? "target.pause" : "service.start", { paused: !state.paused });
        if (reply) acceptStatus(state, reply);
        void refreshNativePair(state);
      } else {
        if (scenario === "failure") { notify(id, "连接暂不可用", true); return; }
        state.paused = !state.paused;
      }
      break;
    case "rename":
      editDevice(panel, deviceId);
      return;
    case "save-name":
      saveName(panel);
      return;
    case "remove": {
      if (state.phase === "remove" && state.activeId === deviceId) {
        if (scenario === "failure") { notify(id, "删除失败", true); return; }
        const index = state.devices.findIndex(device => device.id === deviceId);
        if (index < 0) return;
        if (nativePanel) {
          const reply = await changeNative(state, "device.remove", { id: deviceId });
          if (!reply) return;
          acceptDevices(state, reply);
        } else {
          state.devices.splice(index, 1);
        }
        clearDeviceAction(state);
        render(id, true);
        const adjacent = state.devices[Math.min(index, state.devices.length - 1)];
        if (adjacent) focusCard(panel, adjacent.id, "button.card-name");
        return;
      }
      state.activeId = deviceId;
      state.phase = "remove";
      state.draft = "";
      render(id);
      focusCard(panel, deviceId, "[data-action=remove]");
      return;
    }
    case "cancel-edit":
      clearDeviceAction(state);
      render(id);
      focusCard(panel, deviceId, "button.card-name");
      return;
    case "refresh":
      state.copySequence++;
      state.copied = "";
      window.clearTimeout(state.copyTimer);
      if (nativePanel) void refreshNativePair(state, true);
      else {
        state.pairIndex++;
        state.expiresAt = Date.now() + pairLifetime * 1000;
      }
      break;
    case "copy":
    case "copy-pin":
      await copyPairing(id, control.dataset.action === "copy-pin" ? "pin" : "link");
      return;
    case "update":
      checkUpdate(id);
      return;
    case "uninstall":
      if (!state.uninstallConfirm) {
        state.uninstallConfirm = true;
        render(id);
        window.clearTimeout(state.uninstallTimer);
        state.uninstallTimer = window.setTimeout(() => {
          if (states[id] !== state) return;
          state.uninstallConfirm = false;
          render(id);
        }, 3200);
        return;
      }
      window.clearTimeout(state.uninstallTimer);
      state.uninstallConfirm = false;
      if (nativePanel && !await changeNative(state, "settings.remove")) {
        render(id);
        return;
      }
      state.uninstalled = true;
      render(id);
      return;
    default:
      return;
  }
  render(id);
}

function wirePanelEvents() {
  document.addEventListener("pointerdown", event => {
    const panel = event.target.closest(".panel");
    for (const [id, state] of Object.entries(states)) {
      const samePanel = panel?.dataset.panel === id;
      let changed = false;
      if (state.settings && !(samePanel && event.target.closest(".settings,[data-action=settings]"))) {
        state.settings = false;
        state.uninstallConfirm = false;
        changed = true;
      }
      const removal = event.target.closest("[data-action=remove]")?.closest(".device-card");
      if (state.phase === "remove" && !(samePanel && removal?.dataset.deviceId === state.activeId)) {
        clearDeviceAction(state);
        changed = true;
      }
      if (changed) render(id);
    }
  });
  document.addEventListener("click", event => {
    const control = event.target.closest("button[data-action]");
    const panel = control?.closest(".panel");
    if (panel) void handleAction(panel, control);
  });
  document.addEventListener("input", event => {
    if (event.target.matches(".card-input")) {
      states[event.target.closest(".panel").dataset.panel].draft = event.target.value;
    }
  });
  document.addEventListener("keydown", event => {
    if (event.isComposing) return;
    const panel = event.target.closest?.(".panel") || (nativePanel ? document.querySelector(".panel") : null);
    if (!panel) return;
    const state = states[panel.dataset.panel];
    if (event.key === "Enter" && event.target.matches(".card-input")) {
      event.preventDefault();
      if (!state.busy) void saveName(panel);
      return;
    }
    if (event.key !== "Escape") return;
    if (!state.phase && !state.settings) {
      if (nativePanel) window.chrome.webview.postMessage({ operation: "dismiss" });
      return;
    }
    event.preventDefault();
    const device = state.activeId;
    const settingsOpen = state.settings;
    clearDeviceAction(state);
    state.settings = false;
    state.uninstallConfirm = false;
    render(panel.dataset.panel);
    if (device) focusCard(panel, device, "button.card-name");
    else if (settingsOpen) panel.querySelector("[data-action=settings]").focus({ preventScroll: true });
  });
}
