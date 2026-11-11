const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { pathToFileURL } = require("node:url");
const root = path.resolve(__dirname, "..");
const dependencies = process.env.CHATGPTREMOTE_UI_DEPS || path.join(root, "node_modules");
const { chromium } = require(path.join(dependencies, "playwright-core"));
const batch = path.resolve(process.env.CHATGPTREMOTE_PANEL_TEST_DIR || path.join(root, "_wip/test-panel"));
const documentPath = path.join(root, "Remote-UI.html");
fs.mkdirSync(batch, { recursive: true });
const themes = ["dark", "light"];
const scenarios = ["normal", "single", "three", "many", "empty", "long", "paused", "expired", "update", "failure"];
const scenes = ["home", "pair", "settings", "rename", "remove"];
const ids = ["c"];

async function readPanel(page) {
  return page.locator(".gallery .panel").evaluateAll(panels => panels.map(panel => {
    const { x, y, width, height, left, top, right, bottom } = panel.getBoundingClientRect();
    const clipped = [];
    for (const element of panel.querySelectorAll("button,textarea,.qr-box,.settings")) {
      if (element.closest(".device-track")) continue;
      const bounds = element.getBoundingClientRect();
      if (!bounds.width || !bounds.height) continue;
      if (bounds.left < left || bounds.top < top || bounds.right > right + .5 || bounds.bottom > bottom + .5) {
        clipped.push(element.className || element.textContent);
      }
    }
    return { id: panel.dataset.panel, x, y, width, height, radius: getComputedStyle(panel).borderRadius, clipped };
  }));
}

function boxOnly(rect) {
  return { x: Math.round(rect.x * 100) / 100, y: Math.round(rect.y * 100) / 100,
    width: Math.round(rect.width * 100) / 100, height: Math.round(rect.height * 100) / 100 };
}

function sameBox(a, b) {
  return ["x", "y", "width", "height"].every(key => Math.abs(a[key] - b[key]) < 0.6);
}

async function nameLayout(locator) {
  return locator.evaluate(element => {
    const { x, y, width, height } = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return { x, y, width, height, font: style.font, lineHeight: style.lineHeight, color: style.color,
      textAlign: style.textAlign, padding: style.padding, letterSpacing: style.letterSpacing };
  });
}

async function verifyAlignment(page) {
  await page.selectOption("#scenario-choice", "normal");
  await page.selectOption("#scene-choice", "home");
  const panel = page.locator('.gallery [data-panel="c"]');
  const settle = async () => {
    await panel.evaluate(element => Promise.allSettled(element.getAnimations({ subtree: true }).map(item => item.finished)));
    await page.waitForTimeout(30);
  };
  const box = async selector => panel.locator(selector).first().boundingBox();
  const title = await box(".panel-title");
  const qr = await box(".qr-box");
  const card = await box(".device-card");
  const code = await box(".code-copy");
  const link = await box("[data-action=copy]");
  assert.equal(qr.x, title.x, "二维码左边必须与标题左边对齐");
  assert.equal(qr.y, card.y, "二维码上边必须与设备卡上边对齐");
  assert.equal(qr.y + qr.height, card.y + card.height, "二维码下边必须与设备卡下边对齐");
  assert.equal(code.y + code.height / 2, link.y + link.height / 2, "配对码行与复制链接行必须共用中心线");
  const ringBox = await box(".ring-button");
  const pinBox = await box(".pin");
  const codeBox = await box(".code-copy");
  const centers = [pinBox, ringBox, codeBox, link].map(item => item.y + item.height / 2);
  assert.ok(centers.every(value => Math.abs(value - centers[0]) < 0.6), "配对码、刷新圆环、复制链接必须共用一条水平中线");
  const shellBox = await panel.boundingBox();
  const topGap = title.y - shellBox.y;
  const bottomGap = shellBox.y + shellBox.height - (pinBox.y + pinBox.height);
  assert.ok(Math.abs(topGap - bottomGap) < 1.2, "标题上距必须等于底部下距");
  assert.equal(await page.locator(".gallery .device-menu").count(), 0, "设备操作浮窗必须已移除");
  assert.equal(await page.locator('.gallery [data-panel="c"] .device-card').first().locator("[data-action=remove]").count(), 1, "设备卡必须有删除按钮");
  const track = await box(".device-track");
  const second = await panel.locator(".device-card").nth(1).boundingBox();
  assert.ok(Math.abs(second.x + second.width - track.x - track.width) < 1.2, "两张卡必须刚好铺满设备区");
  assert.ok(Math.abs(link.width - track.width) < 1.2, "复制链接必须占满两张卡的宽度");
  const radii = await Promise.all([
    panel.locator(".qr-box").evaluate(element => getComputedStyle(element).borderRadius),
    panel.locator(".device-card").first().evaluate(element => getComputedStyle(element).borderRadius)
  ]);
  assert.equal(radii[0], radii[1], "二维码圆角必须与设备卡圆角一致");
  await page.selectOption("#scenario-choice", "three");
  await settle();
  const third = await panel.locator(".device-card").nth(2).boundingBox();
  assert.ok(third.x >= track.x + track.width - 1.2, "第三张卡不得露出一部分");
  await page.selectOption("#scenario-choice", "single");
  await settle();
  const only = await panel.locator(".device-card").first().boundingBox();
  assert.equal(only.x, track.x, "单台设备时卡片与设备区左边对齐");
  assert.ok(Math.abs(only.width - track.width) < 1.2, "单台设备时卡片必须占满两张卡的宽度 card=" + only.width + " track=" + track.width);
  assert.equal(qr.x, code.x, "配对码行左边必须与二维码左边对齐");
  await page.selectOption("#scenario-choice", "normal");
  await settle();
}

async function verifyCards(page, id) {
  const panel = page.locator('.gallery [data-panel="' + id + '"]');
  await panel.evaluate(element => Promise.allSettled(element.getAnimations({ subtree: true }).map(item => item.finished)));
  const track = panel.locator(".device-track");
  const first = track.locator(".device-card").first();
  const firstName = first.locator("button.card-name");
  const firstInput = first.locator("textarea");
  const shell = await panel.boundingBox();
  const name = await nameLayout(firstName);
  const firstPosition = await first.boundingBox();
  const header = await panel.locator(".panel-head").boundingBox();
  const body = await panel.locator(".panel-main").boundingBox();
  const second = track.locator(".device-card").nth(1);
  const secondPosition = await second.boundingBox();

  const size = 74;
  assert.equal(firstPosition.width, size);
  assert.equal(firstPosition.height, size);
  await firstName.click();
  assert.ok(sameBox(boxOnly(await nameLayout(firstInput)), boxOnly(name)), id + " name label and editor must occupy the same coordinates");
  assert.deepEqual(await first.boundingBox(), firstPosition);
  assert.deepEqual(await second.boundingBox(), secondPosition);
  assert.deepEqual(await panel.locator(".panel-main").boundingBox(), body);
  await firstInput.fill("常用手机");
  await firstInput.press("Enter");
  assert.equal(await firstName.textContent(), "常用手机");
  assert.deepEqual(await nameLayout(firstName), name);

  await first.locator("[data-action=remove]").click();
  assert.deepEqual(await nameLayout(firstName), name);
  assert.deepEqual(await first.boundingBox(), firstPosition);
  assert.deepEqual(await second.boundingBox(), secondPosition);
  assert.deepEqual(await panel.locator(".panel-head").boundingBox(), header);
  await panel.locator(".panel-title").click();
  assert.deepEqual(await nameLayout(firstName), name);
  assert.deepEqual(await panel.boundingBox(), shell);
  assert.deepEqual(await panel.locator(".panel-main").boundingBox(), body);

  await panel.locator("[data-action=settings]").click();
  assert.deepEqual(await panel.locator(".panel-main").boundingBox(), body);
  assert.deepEqual(await panel.boundingBox(), shell);
  await panel.locator("[data-action=update]").click();
  await panel.getByText("已是最新", { exact: true }).waitFor();
  assert.deepEqual(await panel.locator(".panel-main").boundingBox(), body);
  await panel.locator("[data-action=settings]").click();

  await first.locator("[data-action=remove]").click();
  const transition = await first.locator("[data-action=remove]").evaluate(element => {
    const target = element.closest(".device-track").querySelector(".device-card:nth-child(2)");
    const initialX = target.getBoundingClientRect().left;
    element.click();
    return { initialX, nextX: target.getBoundingClientRect().left, animating: target.getAnimations().some(item => item.effect.target === target) };
  });
  assert.equal(transition.initialX, transition.nextX);
  assert.equal(transition.animating, true);
  await track.locator(".device-card").first().evaluate(element => Promise.allSettled(element.getAnimations().map(item => item.finished)));
  const remaining = await track.locator(".device-card").first().boundingBox();
  assert.deepEqual({ x: remaining.x, y: remaining.y, height: remaining.height },
    { x: firstPosition.x, y: firstPosition.y, height: firstPosition.height });
  const trackBox = await track.boundingBox();
  assert.ok(Math.abs(remaining.width - trackBox.width) < 1.2, "只剩一台设备时卡片占满两张卡的宽度");
  assert.deepEqual(await panel.boundingBox(), shell);
  assert.deepEqual(await panel.locator(".panel-head").boundingBox(), header);
  return { shellStable: true, editorGeometryStable: true, removeConfirmationStable: true, animatedRemoval: true };
}

async function verifyOverflow(page, id) {
  const panel = page.locator('.gallery [data-panel="' + id + '"]');
  const track = panel.locator(".device-track");
  const fades = () => track.evaluate(element => [element.dataset.left, element.dataset.right, Math.round(element.scrollLeft)]);
  const fadeWidths = () => track.evaluate(element => ["--fade-left", "--fade-right"]
    .map(property => parseFloat(element.style.getPropertyValue(property))));
  assert.deepEqual(await fades(), ["false", "true", 0]);
  assert.deepEqual(await fadeWidths(), [0, 8]);
  await track.hover();
  await track.evaluate(element => {
    window.scrollFrames = [];
    element.addEventListener("scroll", () => window.scrollFrames.push(Math.round(element.scrollLeft)));
  });
  await page.mouse.wheel(0, 66);
  await page.waitForFunction(key => Math.round(document.querySelector('.gallery [data-panel="' + key + '"] .device-track').scrollLeft) === 80, id);
  assert.deepEqual((await fades()).slice(0, 2), ["true", "true"]);
  assert.deepEqual(await fadeWidths(), [8, 8]);
  assert.ok(await page.evaluate(() => new Set(window.scrollFrames.filter(value => value > 0 && value < 80)).size >= 4));
  const position = await track.boundingBox();
  const beforeDrag = (await fades())[2];
  await page.mouse.move(position.x + position.width - 14, position.y + 18);
  await page.mouse.down();
  await page.mouse.move(position.x + 15, position.y + 18, { steps: 6 });
  await page.mouse.up();
  assert.ok((await fades())[2] > beforeDrag);
  assert.equal(await panel.locator("textarea:visible").count(), 0);
  await page.waitForFunction(key => {
    const track = document.querySelector('.gallery [data-panel="' + key + '"] .device-track');
    return Math.round(track.scrollLeft) % 80 === 0;
  }, id);
  await track.press("End");
  await page.waitForFunction(key => {
    const track = document.querySelector('.gallery [data-panel="' + key + '"] .device-track');
    return track.style.getPropertyValue("--fade-right") === "0px";
  }, id);
  const [left, right, scroll] = await fades();
  assert.deepEqual([left, right], ["true", "false"]);
  assert.deepEqual(await fadeWidths(), [8, 0]);
  const last = track.locator(".device-card").last();
  const label = last.locator("button.card-name");
  const bounds = await nameLayout(label);
  await label.click();
  assert.ok(sameBox(boxOnly(await nameLayout(last.locator("textarea"))), boxOnly(bounds)));
  assert.equal((await fades())[2], scroll);
  await last.locator("textarea").press("Escape");
  assert.equal((await fades())[2], scroll);
}

async function verifyNavigation(page) {
  const panel = page.locator(".gallery [data-panel=c]");
  const control = panel.locator("[data-action=settings]");
  const before = await control.boundingBox();
  await control.click();
  const dropdown = await panel.locator(".settings").boundingBox();
  const rightArea = await panel.locator(".right-area").boundingBox();
  const rightCard = await panel.locator(".device-card").first().boundingBox();
  assert.ok(Math.abs(dropdown.x - rightArea.x) <= 1, "设置卡必须贴住设备区左边");
  assert.ok(Math.abs(dropdown.y - rightArea.y) <= 1, "设置卡必须贴住设备区上边");
  assert.ok(Math.abs(dropdown.width - rightArea.width) <= 1, "设置卡宽度必须等于两张卡的区域");
  assert.ok(Math.abs(dropdown.height - rightCard.height) <= 1, "设置卡高度必须等于设备卡高度");
  assert.ok(dropdown.x + dropdown.width <= before.x + before.width, "设置卡不得越过面板右内边距");
  await panel.locator("[data-action=update]").press("Escape");
  assert.equal(await panel.locator(".settings").isHidden(), true);
  assert.equal(await control.evaluate(element => element === document.activeElement), true);
}

async function verifyAccent(page) {
  const accents = await page.locator(".gallery .panel").evaluateAll(panels => {
    const allowed = new Set(["ring-button", "ring-arc"]);
    const green = [];
    for (const panel of panels) {
      for (const element of [panel, ...panel.querySelectorAll("*")]) {
        if (!element.getClientRects().length) continue;
        const style = getComputedStyle(element);
        for (const property of ["color", "backgroundColor"]) {
          const rgb = style[property].match(/[\d.]+/g)?.map(Number);
          if (!rgb || (rgb.length > 3 && rgb[3] === 0)) continue;
          if (rgb[1] <= rgb[0] + 20 || rgb[1] <= rgb[2] + 20) continue;
          if (![...element.classList].some(name => allowed.has(name)) && !element.closest(".ring-button")) {
            green.push({ className: element.className.toString(), property, value: style[property] });
          }
        }
      }
    }
    return green;
  });
  assert.deepEqual(accents, []);
}

async function verifyPairingControls(page, id) {
  const panel = page.locator('.gallery [data-panel="' + id + '"]');
  const ring = panel.locator(".ring-button");
  const code = panel.locator(".code-copy");
  const pin = panel.locator(".pin");
  const copy = panel.locator("[data-action=copy]");
  const elements = { pin, code, ring, copy, qr: panel.locator(".qr-box") };
  async function geometry() {
    const entries = await Promise.all(Object.entries(elements).map(async ([key, locator]) => [key, await locator.boundingBox()]));
    return Object.fromEntries(entries);
  }
  const before = await geometry();
  assert.equal(before.qr.width, 74);
  assert.equal(before.qr.height, 74);
  assert.equal(before.code.y + before.code.height / 2, before.ring.y + before.ring.height / 2);
  assert.ok(before.ring.x >= before.code.x + before.code.width);
  assert.equal(await ring.locator(".icon").count(), 0);
  assert.equal(await code.isDisabled(), true);
  assert.equal(await copy.isDisabled(), true);
  await ring.click();
  assert.equal(await ring.getAttribute("data-expired"), "false");
  assert.equal(await code.isEnabled(), true);
  assert.equal(await copy.isEnabled(), true);
  assert.deepEqual(await geometry(), before);
  const original = await pin.textContent();
  await code.click();
  assert.equal(await page.evaluate(() => window.copiedPairingLink), original.replaceAll(" ", ""));
  assert.equal(await code.getAttribute("title"), "配对码已复制");
  assert.deepEqual(await geometry(), before);
  assert.equal(await panel.locator(".notice").isHidden(), true);
  await copy.click();
  assert.match(await page.evaluate(() => window.copiedPairingLink), /^https:\/\/example\.com\/remote\?pair=\d{6}$/);
  assert.equal(await copy.getAttribute("title"), "链接已复制");
  assert.deepEqual(await geometry(), before);
  const image = await panel.locator(".qr-box img").getAttribute("src");
  await ring.click();
  assert.notEqual(await panel.locator(".qr-box img").getAttribute("src"), image);
  assert.notEqual(await pin.textContent(), original);
  assert.deepEqual(await geometry(), before);
}

async function verifyFailures(page) {
  await page.selectOption("#scenario-choice", "failure");
  await page.selectOption("#scene-choice", "home");
  for (const id of ids) {
    const panel = page.locator('.gallery [data-panel="' + id + '"]');
    const first = panel.locator(".device-card").first();
    await first.locator("button.card-name").click();
    const input = first.locator("textarea");
    await input.fill("新的名称");
    await input.press("Enter");
    assert.equal(await input.isVisible(), true);
    assert.equal(await input.inputValue(), "新的名称");
    await panel.getByText("保存失败", { exact: true }).waitFor();
    await first.locator("[data-action=cancel-edit]").click();
    await first.locator("[data-action=remove]").click();
    await first.locator("[data-action=remove]").click();
    await panel.getByText("删除失败", { exact: true }).waitFor();
    assert.equal(await panel.locator(".device-card").count(), 2);
    await panel.locator(".panel-title").click();
    await panel.locator("[data-action=settings]").click();
    await panel.locator("[data-action=update]").click();
    await panel.getByText("检查失败", { exact: true }).waitFor();
    await panel.locator("[data-action=update]").press("Escape");
  }
  await page.selectOption("#scenario-choice", "normal");
  await page.selectOption("#scene-choice", "pair");
  await page.evaluate(() => { window.clipboardFailure = true; });
  for (const id of ids) {
    const copy = page.locator('.gallery [data-panel="' + id + '"] .code-copy');
    await copy.click();
    await page.waitForFunction(key => document.querySelector('.gallery [data-panel="' + key + '"] .code-copy').dataset.error === "true", id);
    assert.equal(await copy.getAttribute("title"), "复制失败");
  }
  await page.evaluate(() => { window.clipboardFailure = false; });
}

async function verifyActionsAndMotion(page) {
  await page.selectOption("#scenario-choice", "update");
  await page.selectOption("#scene-choice", "settings");
  for (const id of ids) {
    const panel = page.locator('.gallery [data-panel="' + id + '"]');
    if (await panel.locator(".settings").isHidden()) await panel.locator("[data-action=settings]").click();
    const menu = await panel.locator(".settings").boundingBox();
    await panel.locator("[data-action=update]").click();
    await panel.getByText("安装更新", { exact: true }).waitFor();
    await panel.locator("[data-action=update]").click();
    assert.equal(await panel.getByText("已安装", { exact: true }).count(), 1);
    assert.deepEqual(await panel.locator(".settings").boundingBox(), menu);
    await panel.locator("[data-action=uninstall]").click();
    await panel.getByText("再点一次卸载", { exact: true }).waitFor();
    assert.deepEqual(await panel.locator(".settings").boundingBox(), menu, "卸载确认不得改变设置卡尺寸");
    await panel.locator("[data-action=uninstall]").click();
    await panel.getByText("已卸载", { exact: true }).waitFor();
    assert.deepEqual(await panel.locator(".settings").boundingBox(), menu, "卸载完成不得改变设置卡尺寸");
  }
  await page.selectOption("#scenario-choice", "single");
  await page.selectOption("#scene-choice", "home");
  for (const id of ids) {
    const panel = page.locator('.gallery [data-panel="' + id + '"]');
    const card = panel.locator(".device-card");
    await card.locator("[data-action=remove]").click();
    await card.locator("[data-action=remove]").click();
    await panel.locator(".empty").waitFor({ state: "visible" });
    assert.equal(await panel.locator(".device-card").count(), 0);
    assert.equal(await panel.locator(".notice").isHidden(), true);
  }
  await page.locator("#reset").click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const id of ids) {
    const panel = page.locator('.gallery [data-panel="' + id + '"]');
    const first = panel.locator(".device-card").first();
    await first.locator("[data-action=remove]").click();
    await first.locator("[data-action=remove]").click();
    assert.equal(await panel.locator(".device-card").first().evaluate(element => element.getAnimations().length), 0);
    await panel.locator("[data-action=settings]").click();
    await page.locator("h1").click();
    assert.equal(await panel.locator(".settings").isHidden(), true);
  }
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.locator("#reset").click();
}

async function run() {
  const browser = await chromium.launchPersistentContext(path.join(batch, "browser-profile"), {
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: true,
    viewport: { width: 1240, height: 780 },
    env: { ...process.env, TEMP: batch, TMP: batch },
    args: ["--disable-background-networking", "--disable-component-update"]
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", {
      value: { writeText: async value => {
        if (window.clipboardFailure) throw new Error("clipboard denied");
        window.copiedPairingLink = value;
      } }
    }));
    await page.goto(pathToFileURL(documentPath).href);
    await page.locator(".gallery .panel").last().waitFor();
    const firstLayout = await readPanel(page);
    const cases = [];
    for (const theme of themes) {
      await page.locator('.theme-switch [data-theme="' + theme + '"]').click();
      for (const scenario of scenarios) {
        await page.selectOption("#scenario-choice", scenario);
        for (const scene of scenes) {
          await page.selectOption("#scene-choice", scene);
          const current = await readPanel(page);
          assert.deepEqual(current, firstLayout, [theme, scenario, scene].join("/"));
          cases.push(...current.map(panel => ({ theme, scenario, scene, id: panel.id })));
        }
      }
    }
    await page.locator("#reset").click();
    assert.equal(await page.locator('.gallery [data-panel="c"] .device-card button.card-name').first().textContent(), "我的手机");
    await verifyAccent(page);
    await page.screenshot({ path: path.join(batch, "light-home.png") });
    await page.locator('.theme-switch [data-theme="dark"]').click();
    await verifyAccent(page);
    const nameBoxes = await page.locator(".gallery .device-card:first-child button.card-name").evaluateAll(elements =>
      elements.map(element => {
        const bounds = element.getBoundingClientRect();
        return { id: element.closest(".panel").dataset.panel,
          x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
      }));
    fs.writeFileSync(path.join(batch, "name-boxes.json"), JSON.stringify(nameBoxes));
    await page.screenshot({ path: path.join(batch, "dark-home.png") });
    await page.selectOption("#scene-choice", "rename");
    await page.screenshot({ path: path.join(batch, "dark-rename.png") });
    await page.selectOption("#scene-choice", "remove");
    await page.screenshot({ path: path.join(batch, "dark-remove.png") });
    await page.selectOption("#scene-choice", "settings");
    await page.screenshot({ path: path.join(batch, "dark-settings.png") });
    await page.locator("#reset").click();
    await verifyNavigation(page);
    await verifyAlignment(page);

    const geometry = {};
    for (const id of ids) geometry[id] = await verifyCards(page, id);
    await page.selectOption("#scenario-choice", "many");
    for (const id of ids) {
      const names = await page.locator('.gallery [data-panel="' + id + '"] .device-card button.card-name').allTextContents();
      assert.deepEqual(names.slice(0, 3), ["我的手机", "备用手机", "手机 3"]);
    }
    for (const id of ids) await verifyOverflow(page, id);
    await page.screenshot({ path: path.join(batch, "dark-carousel.png") });

    await page.selectOption("#scenario-choice", "expired");
    await page.selectOption("#scene-choice", "pair");
    for (const id of ids) await verifyPairingControls(page, id);
    await page.screenshot({ path: path.join(batch, "dark-pair.png") });
    await verifyFailures(page);
    await verifyActionsAndMotion(page);
    await page.selectOption("#scene-choice", "pair");
    await page.locator("#simulate-pair").click();
    assert.equal(await page.locator('.gallery [data-panel="c"] .device-card').count(), 3);
    await page.locator('[data-context="c"]').click();
    await page.locator("#context [data-panel=c]").waitFor();
    await page.screenshot({ path: path.join(batch, "context.png") });
    await page.locator("#close-context").click();
    await page.locator("#preview-mobile").click();
    const mobile = page.frameLocator("#mobile-frame");
    await mobile.locator("#startupView").waitFor({ state: "visible" });
    assert.equal(await mobile.locator("#startupView .loading-dots span").count(), 3);
    await mobile.locator("#startupView").waitFor({ state: "hidden" });
    await page.locator("#close-context").click();
    await page.setViewportSize({ width: 320, height: 850 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320);
    await page.screenshot({ path: path.join(batch, "narrow.png"), fullPage: true });
    assert.deepEqual(errors, []);
    const report = { layouts: cases.length, geometry, scrollFadesAndDrag: true, pinAndLinkCopy: true, pureRefreshRing: true,
      settingsAnchoredToGear: true, settingsCardAligned: true, neutralControls: true,
      failuresRetainEdits: true,
      emptyAfterRemoval: true, updateAndUninstall: true, reducedMotion: true,
      pairedCount: 3, mobilePreview: true, narrowViewport: 320, errors };
    fs.writeFileSync(path.join(batch, "verification.json"), JSON.stringify(report, null, 2));
    process.stdout.write(JSON.stringify(report) + "\n");
  } finally {
    await browser.close();
  }
}

run().catch(error => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
