const gallery = document.getElementById("gallery");
for (const [id, layout] of Object.entries(layouts)) {
  states[id] = newState();
  const article = document.createElement("article");
  article.className = "scheme";
  article.innerHTML = '<div class="scheme-heading"><h2>' + layout.name + "</h2></div>"
    + '<div class="stage">' + rail() + '<div class="stage-caption">' + icon("panel-left") + 'ChatGPT</div><div class="mount"></div></div>'
    + '<div class="scheme-actions"><button class="control" data-context="' + id + '">' + icon("arrow-up-right")
    + '在 ChatGPT 中预览</button></div>';
  gallery.append(article);
  mountPanel(article.querySelector(".mount"), id);
}
updateMarks();
wirePanelEvents();
document.addEventListener("click", event => {
  const control = event.target.closest("button");
  if (control?.dataset.context) showDesktop(control.dataset.context);
  if (!control?.matches(".theme-switch button")) return;
  document.documentElement.dataset.theme = control.dataset.theme;
  document.querySelectorAll(".theme-switch button").forEach(button => button.setAttribute("aria-pressed", String(button === control)));
  updateMarks();
});
const sceneChoice = document.getElementById("scene-choice");
const scenarioChoice = document.getElementById("scenario-choice");
function resetStates() {
  for (const id of Object.keys(layouts)) {
    window.clearTimeout(states[id].noticeTimer);
    window.clearTimeout(states[id].copyTimer);
    states[id] = newState();
    document.querySelectorAll('[data-panel="' + id + '"] .device-track').forEach(track => { track.scrollLeft = 0; });
    setScene(id, sceneChoice.value);
  }
}
sceneChoice.addEventListener("change", () => {
  for (const id of Object.keys(layouts)) {
    document.querySelectorAll('[data-panel="' + id + '"] .device-track').forEach(track => { track.scrollLeft = 0; });
    setScene(id, sceneChoice.value);
  }
});
scenarioChoice.addEventListener("change", () => { scenario = scenarioChoice.value; resetStates(); });
document.getElementById("reset").addEventListener("click", () => {
  scenario = scenarioChoice.value = "normal";
  sceneChoice.value = "home";
  resetStates();
});
document.getElementById("simulate-pair").addEventListener("click", () => {
  for (const [id, state] of Object.entries(states)) {
    if (Date.now() >= state.expiresAt || state.paused) continue;
    state.devices.push({ id: String(Math.max(0, ...state.devices.map(device => Number(device.id))) + 1), name: "新手机" });
    state.pairIndex++;
    state.expiresAt = Date.now() + pairLifetime * 1000;
    setScene(id, "home");
    notify(id, "配对成功");
  }
});
document.getElementById("close-context").addEventListener("click", () => dialog.close());
document.getElementById("preview-mobile").addEventListener("click", showMobile);
document.getElementById("replay-mobile").addEventListener("click", showMobile);
dialog.addEventListener("close", () => { contextApp.replaceChildren(); });
window.setInterval(() => {
  for (const [id, state] of Object.entries(states)) {
    if (scenario !== "expired" && !state.paused && Date.now() >= state.expiresAt) {
      state.pairIndex++;
      state.expiresAt = Date.now() + pairLifetime * 1000;
      state.copied = "";
      window.clearTimeout(state.copyTimer);
      render(id);
    } else {
      document.querySelectorAll('[data-panel="' + id + '"]').forEach(panel => paintPairing(panel, state));
    }
  }
}, 250);
