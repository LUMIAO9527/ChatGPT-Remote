const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const [flag, destination] = process.argv.slice(2);
if (flag && (flag !== "--native" || !destination)) throw new Error("Usage: node tools/build_panel.cjs [--native output.html]");
const native = flag === "--native";
const packages = process.env.CHATGPTREMOTE_UI_DEPS || path.join(root, "node_modules");
const names = [
  "settings", "chevron-left", "ellipsis", "smartphone", "pencil", "trash-2",
  "check", "x", "qr-code", "plus", "refresh-cw", "copy", "pause", "play",
  "circle-alert", "house", "panels-top-left", "history", "panel-left",
  "circle-gauge", "arrow-up-right", "square-pen", "folder"
];
const icons = Object.fromEntries(names.map(name => [
  name, fs.readFileSync(path.join(packages, "lucide-static/icons", name + ".svg"), "utf8")
]));
function dataAsset(file) {
  const bytes = fs.readFileSync(path.join(root, "WindowsAgent/assets", file));
  return "data:image/svg+xml;base64," + bytes.toString("base64");
}
const versionSource = fs.readFileSync(path.join(root, "WindowsAgent/app_version.py"), "utf8");
const version = versionSource.match(/APP_VERSION\s*=\s*['"]([^'"]+)['"]/)[1];
const assets = { icons, version, mark: dataAsset("rm-mark-dark.svg"), markLight: dataAsset("rm-mark-light.svg") };
if (!native) {
  const qrcode = require(path.join(packages, "qrcode-generator"));
  assets.pairs = ["324856", "862149", "517203"].map(pin => {
    const qr = qrcode(0, "M");
    const url = "https://example.com/remote?pair=" + pin;
    qr.addData(url);
    qr.make();
    return { pin, url, qr: qr.createDataURL(5, 20) };
  });
  assets.mobileHtml = fs.readFileSync(path.join(root, "WindowsAgent/web/index.html"), "utf8")
    .replace(/\/icon\.(?:svg|png)(?:\?[^"\s]*)?/g, dataAsset("rm-mark-light.svg"));
  assets.mobileFixture = fs.readFileSync(path.join(root, "tools/review-fixtures.js"), "utf8");
}
const read = file => fs.readFileSync(path.join(root, "ui", file), "utf8");
const styles = [read("styles/theme.css"), read("styles/panel.css"), native ? "" : read("styles/review.css")].join("\n");
const script = [read("scripts/panel.js"), read(native ? "scripts/native.js" : "scripts/review.js")].join("\n\n");
const template = read(native ? "native.html" : "shell.html")
  .replace("__STYLES__", () => styles)
  .replace("__SCRIPT__", () => script);
const license = fs.readFileSync(path.join(packages, "lucide-static/LICENSE"), "utf8");
const html = template.replace("__REMOTE_ASSET_DATA__", () => JSON.stringify(assets).replaceAll("<","\\u003c"))
  .replace("__REMOTE_LICENSES__", () => license.replaceAll("--", "—"));
const output = native ? path.resolve(destination) : path.join(root, "Remote-UI.html");
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, html);
process.stdout.write(JSON.stringify({ bytes: Buffer.byteLength(html), output }) + "\n");
