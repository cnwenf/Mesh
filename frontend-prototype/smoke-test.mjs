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

const initial = await evaluate(`({
  title: document.title,
  issueRows: document.querySelectorAll(".data-table tbody tr").length
})`);
assert(initial.title === "Issues · Mesh", "Issue page title did not render.");
assert(initial.issueRows >= 6, "Issue table is incomplete.");
checks.push("issue-list");

await evaluate(`document.querySelector('[data-action="open-command"]').click()`);
assert(await evaluate(`Boolean(document.querySelector(".dialog--command"))`), "Command palette did not open.");
await evaluate(`document.querySelector('[data-command-route="board"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 80))`);
assert(await evaluate(`location.hash === "#/board"`), "Command navigation did not reach the board.");
checks.push("command-palette");

const dragResult = await evaluate(`(() => {
  const source = document.querySelector('[data-card-key="MES-145"]');
  const destination = document.querySelector('[data-drop-column="progress"]');
  const transfer = new DataTransfer();
  source.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: transfer }));
  destination.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  destination.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  return document.querySelector('[data-drop-column="progress"]').textContent.includes("MES-145");
})()`);
assert(dragResult, "Board drag and drop did not move the card.");
checks.push("board-drag");

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
  "skills",
  "skill",
  "automations",
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

const beforeTheme = await evaluate(`document.documentElement.dataset.theme`);
await evaluate(`document.querySelector('[data-action="toggle-theme"]').click()`);
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
await evaluate(`(() => {
  const form = document.querySelector('[data-form="login"]');
  form.elements.email.value = "test@mesh.local";
  form.requestSubmit();
})()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(await evaluate(`location.hash === "#/code"`), "Login did not advance to the code step.");
await evaluate(`document.querySelector('[data-form="verify-code"]').requestSubmit()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(await evaluate(`location.hash === "#/issues"`), "Code verification did not enter the workspace.");
checks.push("auth-flow");

socket.close();
console.log(JSON.stringify({ ok: true, checks }, null, 2));
