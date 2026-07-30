/**
 * Dependency-free browser smoke test.
 *
 * Start Chrome with a DevTools port, then run:
 *   node smoke-test.mjs
 */

import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const WebSocketClient = globalThis.WebSocket ?? require("ws");
const endpoint = process.env.MESH_CDP_ENDPOINT ?? "http://127.0.0.1:9222";
const targets = await fetch(`${endpoint}/json/list`).then((response) => response.json());
const target = targets.find((item) => item.type === "page");

if (!target?.webSocketDebuggerUrl) {
  throw new Error("No debuggable page was found.");
}

const socket = new WebSocketClient(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 0;
const pending = new Map();

socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data));
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result);
});

function command(method, params = {}) {
  nextId += 1;
  const id = nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

async function evaluate(expression) {
  const response = await command("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.text);
  }
  return response.result.value;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

await command("Runtime.enable");
const checks = [];

await evaluate(`document.fonts.ready.then(() => true)`);
const fontState = await evaluate(`({
  interLoaded: document.fonts.check("14px Inter"),
  loadedFaces: [...document.fonts].filter((face) => face.family === "Inter" && face.status === "loaded").length,
  bodyFamily: getComputedStyle(document.body).fontFamily
})`);
assert(fontState.interLoaded, "Bundled Inter font did not load.");
assert(fontState.loadedFaces >= 1, "No loaded Inter font face was found.");
assert(fontState.bodyFamily.startsWith("Inter"), "Inter is not the primary UI font.");
checks.push("offline-font");

const initial = await evaluate(`({
  title: document.title,
  boardColumns: document.querySelectorAll("[data-drop-column]").length,
  emptyColumns: document.querySelectorAll(".board-empty").length
})`);
assert(initial.title === "Issues · Mesh", "Issue page title did not render.");
assert(initial.boardColumns === 4, "Default issue board is incomplete.");
assert(initial.emptyColumns === 4, "Default issue board should show four empty states.");
checks.push("issue-board");

await evaluate(`document.querySelector('[data-action="open-command"]').click()`);
assert(await evaluate(`Boolean(document.querySelector(".dialog--command"))`), "Command palette did not open.");
await evaluate(`document.querySelector('[data-command-route="projects"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 80))`);
assert(await evaluate(`location.hash === "#/projects"`), "Command navigation did not reach projects.");
checks.push("command-palette");

await evaluate(`location.hash = "#/issues"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 80))`);
await evaluate(`document.querySelector('[data-action="open-create-issue"]').click()`);
await evaluate(`(() => {
  const form = document.querySelector('[data-form="create-issue"]');
  form.elements.title.value = "浏览器冒烟测试 Issue";
  form.elements.status.value = "todo";
  form.requestSubmit();
})()`);
assert(
  await evaluate(`document.body.textContent.includes("浏览器冒烟测试 Issue")`),
  "Issue creation did not update the board.",
);
checks.push("issue-create");

const dragResult = await evaluate(`(() => {
  const source = document.querySelector('[data-card-key="MES-149"]');
  const destination = document.querySelector('[data-drop-column="progress"]');
  const transfer = new DataTransfer();
  source.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: transfer }));
  destination.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  destination.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  return document.querySelector('[data-drop-column="progress"]').textContent.includes("MES-149");
})()`);
assert(dragResult, "Board drag and drop did not move the card.");
checks.push("board-drag");

const routes = [
  "inbox",
  "chat",
  "my",
  "issues",
  "board",
  "issue",
  "projects",
  "project",
  "members",
  "agents",
  "agent",
  "squads",
  "runtimes",
  "skills",
  "skill",
  "autopilot",
  "automations",
  "usage",
  "analytics",
  "settings",
  "states",
];

for (const route of routes) {
  await evaluate(`location.hash = "#/${route}"`);
  await evaluate(`new Promise(resolve => setTimeout(resolve, 35))`);
  const rendered = await evaluate(`({
    hasPage: Boolean(document.querySelector(".page-layout")),
    title: document.title
  })`);
  assert(rendered.hasPage, `Route ${route} did not render a page.`);
  assert(rendered.title.endsWith("· Mesh"), `Route ${route} has an invalid title.`);
}
checks.push("all-routes");

await evaluate(`location.hash = "#/chat"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 50))`);
await evaluate(`document.querySelector('[data-action="select-chat"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 50))`);
const beforeMessages = await evaluate(`document.querySelectorAll(".message").length`);
await evaluate(`(() => {
  const form = document.querySelector('[data-form="chat"]');
  form.elements.message.value = "请执行冒烟检查";
  form.requestSubmit();
})()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 800))`);
const afterMessages = await evaluate(`document.querySelectorAll(".message").length`);
assert(afterMessages === beforeMessages + 2, "Chat did not append the user and agent messages.");
checks.push("chat-send");

const darkBubble = await evaluate(`(() => {
  document.documentElement.dataset.theme = "dark";
  const style = getComputedStyle(document.querySelector(".message--me .message__body"));
  return { background: style.backgroundColor, color: style.color };
})()`);
assert(darkBubble.background === "rgb(39, 39, 42)", "Dark user message bubble does not match the selected surface.");
assert(darkBubble.color === "rgb(250, 250, 250)", "Dark user message bubble text has the wrong contrast.");
await evaluate(`document.documentElement.dataset.theme = "light"`);
checks.push("dark-chat-bubble");

await evaluate(`location.hash = "#/settings"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 50))`);
await evaluate(`document.querySelector('[data-tab="preferences"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 50))`);
const beforeTheme = await evaluate(`document.documentElement.dataset.theme`);
await evaluate(`(() => {
  const select = document.querySelector("[data-theme-select]");
  select.value = select.value === "dark" ? "light" : "dark";
  select.dispatchEvent(new Event("change", { bubbles: true }));
})()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 50))`);
const afterTheme = await evaluate(`document.documentElement.dataset.theme`);
assert(beforeTheme !== afterTheme, "Theme switch did not change the active theme.");
checks.push("theme-toggle");

await command("Emulation.setDeviceMetricsOverride", {
  width: 390,
  height: 844,
  deviceScaleFactor: 1,
  mobile: true,
});
await evaluate(`location.hash = "#/issues"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 60))`);
const mobileLayout = await evaluate(`({
  bottomNav: getComputedStyle(document.querySelector(".mobile-tabs")).display,
  menuButton: getComputedStyle(document.querySelector(".mobile-menu-button")).display
})`);
assert(mobileLayout.bottomNav === "grid", "Mobile bottom navigation is not visible.");
assert(mobileLayout.menuButton !== "none", "Mobile menu trigger is not visible.");
await evaluate(`document.querySelector('[data-action="mobile-menu"]').click()`);
assert(await evaluate(`Boolean(document.querySelector(".mobile-drawer"))`), "Mobile drawer did not open.");
await evaluate(`document.querySelector(".drawer-backdrop").click()`);
await command("Emulation.clearDeviceMetricsOverride");
checks.push("mobile-navigation");

await evaluate(`location.hash = "#/login"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
const loginInitial = await evaluate(`({
  disabled: document.querySelector('[data-form="login"] [data-auth-submit]').disabled,
  disabledBackground: getComputedStyle(document.querySelector('[data-form="login"] [data-auth-submit]')).backgroundColor,
  expectedDisabledBackground: (() => {
    const probe = document.createElement("span");
    probe.style.backgroundColor = "var(--disabled)";
    document.body.append(probe);
    const background = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return background;
  })(),
  hasBrandAboveCard: Boolean(document.querySelector(".auth-brand")),
  footer: document.querySelector(".auth-card__footer").textContent.replace(/\\s+/g, " ").trim()
})`);
assert(loginInitial.disabled, "Empty login form did not render a disabled primary action.");
assert(loginInitial.disabledBackground === loginInitial.expectedDisabledBackground, "Empty login form did not use the disabled surface.");
assert(!loginInitial.hasBrandAboveCard, "Login page still renders branding above the card.");
assert(loginInitial.footer.includes("Prefer the desktop app?") && loginInitial.footer.includes("Download"), "Login footer CTA is not aligned.");
await evaluate(`(() => {
  const form = document.querySelector('[data-form="login"]');
  const input = form.elements.email;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(input, "test@mesh.local");
  input.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
})()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(await evaluate(`location.hash === "#/code"`), "Login did not advance to the code step.");
await evaluate(`(() => {
  const form = document.querySelector('[data-form="verify-code"]');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  [...form.querySelectorAll(".otp-cell")].forEach((input, index) => {
    setter.call(input, String(index + 1));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  form.requestSubmit();
})()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(await evaluate(`location.hash === "#/issues"`), "Code verification did not enter the workspace.");
checks.push("auth-flow");

socket.close();
console.log(JSON.stringify({ ok: true, checks }, null, 2));
