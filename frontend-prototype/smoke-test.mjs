/**
 * Browser smoke and security regression test.
 *
 * Start an isolated Chrome instance as documented in README.md, then run:
 *   npm test
 */

import WebSocketClient from "ws";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function parseCdpEndpoint(value) {
  let endpoint;
  try {
    endpoint = new URL(value);
  } catch {
    throw new Error("MESH_CDP_ENDPOINT must be a valid URL.");
  }
  assert(endpoint.protocol === "http:", "CDP endpoint must use http://.");
  assert(endpoint.hostname === "127.0.0.1", "CDP endpoint must bind to 127.0.0.1.");
  assert(Boolean(endpoint.port), "CDP endpoint must include an explicit port.");
  assert(!endpoint.username && !endpoint.password, "CDP endpoint must not contain credentials.");
  assert(endpoint.pathname === "/" && !endpoint.search && !endpoint.hash, "CDP endpoint must not contain a path, query, or fragment.");
  return endpoint;
}

function normalizePrototypeUrl(value) {
  const url = new URL(value);
  url.hash = "";
  return url.href;
}

const endpoint = parseCdpEndpoint(process.env.MESH_CDP_ENDPOINT ?? "http://127.0.0.1:9222");
const targetListUrl = new URL("/json/list", endpoint);
const targetResponse = await fetch(targetListUrl, { signal: AbortSignal.timeout(5000) });
assert(targetResponse.ok, `CDP target list failed with HTTP ${targetResponse.status}.`);
const targets = await targetResponse.json();
assert(Array.isArray(targets), "CDP target list returned an invalid payload.");

const prototypeUrl = new URL("./index.html", import.meta.url).href;
const target = targets.find((item) => {
  if (item?.type !== "page" || typeof item.url !== "string") return false;
  try {
    return normalizePrototypeUrl(item.url) === prototypeUrl;
  } catch {
    return false;
  }
});
assert(target?.webSocketDebuggerUrl, "The CDP endpoint does not expose this prototype's index.html page.");
assert(typeof target.id === "string" && target.id.length > 0, "The selected CDP page is missing a target id.");

const debuggerUrl = new URL(target.webSocketDebuggerUrl);
assert(debuggerUrl.protocol === "ws:", "CDP WebSocket must use ws://.");
assert(debuggerUrl.hostname === endpoint.hostname && debuggerUrl.port === endpoint.port, "CDP WebSocket must be same-origin with the approved endpoint.");
assert(!debuggerUrl.username && !debuggerUrl.password && !debuggerUrl.search && !debuggerUrl.hash, "CDP WebSocket URL contains unexpected components.");
assert(debuggerUrl.pathname === `/devtools/page/${target.id}`, "CDP WebSocket does not match the selected prototype page.");

const socket = new WebSocketClient(debuggerUrl, {
  handshakeTimeout: 5000,
  maxPayload: 1024 * 1024,
  perMessageDeflate: false,
});
await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => {
    socket.terminate();
    reject(new Error("Timed out connecting to the approved CDP page."));
  }, 5000);
  socket.once("open", () => {
    clearTimeout(timeout);
    resolve();
  });
  socket.once("error", (error) => {
    clearTimeout(timeout);
    reject(error);
  });
});

try {
let nextId = 0;
const pending = new Map();

socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data));
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject, timeout } = pending.get(message.id);
  pending.delete(message.id);
  clearTimeout(timeout);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result);
});

function command(method, params = {}) {
  nextId += 1;
  const id = nextId;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP command timed out: ${method}`));
    }, 5000);
    pending.set(id, { resolve, reject, timeout });
    socket.send(JSON.stringify({ id, method, params }));
  });
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

await command("Runtime.enable");
const checks = [];

const securityPolicy = await evaluate(`document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.content || ""`);
for (const directive of [
  "default-src 'none'",
  "script-src 'self'",
  "script-src-attr 'none'",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
]) {
  assert(securityPolicy.includes(directive), `Content Security Policy is missing: ${directive}`);
}
checks.push("content-security-policy");

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
  emptyColumns: document.querySelectorAll(".board-empty").length,
  columnDots: document.querySelectorAll(".board-column__dot").length,
  columnActions: document.querySelectorAll("[data-board-action]").length,
  dotColors: [...document.querySelectorAll(".board-column__dot")].map((dot) => getComputedStyle(dot).color)
})`);
assert(initial.title === "Issues · Mesh", "Issue page title did not render.");
assert(initial.boardColumns === 4, "Default issue board is incomplete.");
assert(initial.emptyColumns === 4, "Default issue board should show four empty states.");
assert(initial.columnDots === 4, "Issue board column status dots are incomplete.");
assert(initial.columnActions === 8, "Issue board column actions are incomplete.");
assert(initial.dotColors[0] === initial.dotColors[1], "Neutral issue statuses should share a dot color.");
assert(initial.dotColors[2] !== initial.dotColors[0], "In Progress is missing its warning status color.");
assert(initial.dotColors[3] !== initial.dotColors[2], "In Review is missing its success status color.");
await evaluate(`document.querySelector('[data-board-action="more"]').click()`);
assert(await evaluate(`Boolean(document.querySelector(".board-menu"))`), "Board column more action did not open its menu.");
await evaluate(`document.querySelector(".board-menu-layer").click()`);
await evaluate(`document.querySelector('[data-board-action="add"]').click()`);
assert(await evaluate(`Boolean(document.querySelector('[data-form="create-issue"]'))`), "Board column add action did not open issue creation.");
await evaluate(`document.querySelector('[data-action="close-overlay"]').click()`);
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

const maliciousTitles = [
  `<img src=x onerror="document.body.dataset.xss='img'">`,
  `<svg onload="document.body.dataset.xss='svg'"></svg>`,
  `</div><img src=x onerror="document.body.dataset.xss='closed'"><div>`,
  `\"><script>document.body.dataset.xss='script'</script><span data-break=\"`,
  `<b title="'&\"">quoted & tagged</b>`,
];

await evaluate(`delete document.body.dataset.xss`);
for (const title of maliciousTitles) {
  await evaluate(`document.querySelector('[data-action="open-create-issue"]').click()`);
  await evaluate(`(() => {
    const form = document.querySelector('[data-form="create-issue"]');
    form.elements.title.value = ${JSON.stringify(title)};
    form.elements.status.value = "todo";
    form.requestSubmit();
  })()`);
  await evaluate(`new Promise(resolve => setTimeout(resolve, 120))`);
  const result = await evaluate(`(() => {
    const expected = ${JSON.stringify(title)};
    const card = [...document.querySelectorAll(".board-card")]
      .find((item) => item.querySelector(".board-card__title")?.textContent === expected);
    const titleNode = card?.querySelector(".board-card__title");
    const descendants = card ? [...card.querySelectorAll("*")] : [];
    return {
      found: Boolean(card),
      exactText: titleNode?.textContent === expected,
      titleChildren: titleNode?.children.length ?? -1,
      executableNodes: card?.querySelectorAll("img, svg, script, iframe, object, embed, math").length ?? -1,
      eventAttributes: descendants.flatMap((node) => [...node.attributes]).filter((attribute) => attribute.name.toLowerCase().startsWith("on")).length,
      executed: document.body.dataset.xss || ""
    };
  })()`);
  assert(result.found && result.exactText, "A hostile issue title did not render as literal text.");
  assert(result.titleChildren === 0, "A hostile issue title created child elements.");
  assert(result.executableNodes === 0, "A hostile issue title created an executable node.");
  assert(result.eventAttributes === 0, "A hostile issue title created an event attribute.");
  assert(!result.executed, "A hostile issue title executed script in the page context.");
}

await evaluate(`location.hash = "#/project"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 80))`);
const projectTitleSafety = await evaluate(`(() => {
  const expected = ${JSON.stringify(maliciousTitles)};
  const rows = [...document.querySelectorAll(".issue-row__title")];
  return expected.every((title) => {
    const row = rows.find((item) => item.textContent === title);
    return row && row.children.length === 0 && ![...row.attributes].some((attribute) => attribute.name.toLowerCase().startsWith("on"));
  });
})()`);
assert(projectTitleSafety, "Project issue list did not preserve hostile titles as literal text.");
await evaluate(`location.hash = "#/issues"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 80))`);
checks.push("hostile-title-xss");

for (const [field, value] of [
  ["status", "done<script>"],
  ["priority", "urgent\" onmouseover=alert(1)"],
  ["assignee", "unknown-agent"],
  ["project", "<svg onload=alert(1)>"],
]) {
  const before = await evaluate(`document.querySelectorAll(".board-card").length`);
  await evaluate(`document.querySelector("#toast-region").replaceChildren()`);
  await evaluate(`document.querySelector('[data-action="open-create-issue"]').click()`);
  await evaluate(`(() => {
    const form = document.querySelector('[data-form="create-issue"]');
    const field = form.elements[${JSON.stringify(field)}];
    field.append(new Option("tampered", ${JSON.stringify(value)}, true, true));
    form.elements.title.value = "字段边界回归";
    form.requestSubmit();
  })()`);
  await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
  const rejected = await evaluate(`({
    count: document.querySelectorAll(".board-card").length,
    dialogOpen: Boolean(document.querySelector('[data-form="create-issue"]')),
    feedback: document.querySelector("#toast-region")?.textContent || ""
  })`);
  assert(rejected.count === before, `Unsupported ${field} value created an issue.`);
  assert(rejected.dialogOpen, `Unsupported ${field} value closed the issue form.`);
  assert(rejected.feedback.includes("不受支持"), `Unsupported ${field} value did not show validation feedback.`);
  await evaluate(`document.querySelector('[data-action="close-overlay"]').click()`);
}
checks.push("issue-field-boundaries");

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
  setter.call(input, "tester@example.test");
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

console.log(JSON.stringify({ ok: true, checks }, null, 2));
} finally {
await new Promise((resolve) => {
  const timeout = setTimeout(() => {
    socket.terminate();
    resolve();
  }, 1000);
  socket.once("close", () => {
    clearTimeout(timeout);
    resolve();
  });
  socket.close();
});
}
