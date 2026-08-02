/**
 * Browser smoke and security regression test.
 *
 * Start an isolated Chrome instance as documented in README.md, then run:
 *   npm test
 */

import WebSocketClient from "ws";
import { mkdir, readFile, writeFile } from "node:fs/promises";

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
await command("Page.enable");
await command("Profiler.enable");
await command("Profiler.startPreciseCoverage", { callCount: true, detailed: true });
const checks = [];
const captureVisuals = process.env.MESH_CAPTURE_VISUALS === "1";
const visualOutput = new URL("./visual-artifacts/", import.meta.url);
let visualArtifactCount = 0;
if (captureVisuals) await mkdir(visualOutput, { recursive: true });

const securityPolicy = await evaluate(`document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.content || ""`);
const expectedSecurityPolicy = new Map([
  ["default-src", ["'none'"]],
  ["script-src", ["'self'"]],
  ["script-src-attr", ["'none'"]],
  ["style-src", ["'self'", "'unsafe-inline'"]],
  ["img-src", ["'self'", "data:"]],
  ["font-src", ["'self'"]],
  ["connect-src", ["'none'"]],
  ["object-src", ["'none'"]],
  ["base-uri", ["'none'"]],
  ["form-action", ["'none'"]],
  ["frame-src", ["'none'"]],
  ["media-src", ["'none'"]],
  ["worker-src", ["'none'"]],
]);
const actualSecurityPolicy = new Map();
for (const rawDirective of securityPolicy.split(";").map((value) => value.trim()).filter(Boolean)) {
  const [name, ...sources] = rawDirective.split(/\s+/);
  assert(!actualSecurityPolicy.has(name), `Content Security Policy repeats directive: ${name}`);
  actualSecurityPolicy.set(name, sources);
}
assert(actualSecurityPolicy.size === expectedSecurityPolicy.size, "Content Security Policy contains missing or unexpected directives.");
for (const [name, expectedSources] of expectedSecurityPolicy) {
  const actualSources = actualSecurityPolicy.get(name);
  assert(
    JSON.stringify(actualSources) === JSON.stringify(expectedSources),
    `Content Security Policy directive ${name} must be exactly: ${expectedSources.join(" ")}`,
  );
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
const commandAccessibility = await evaluate(`(() => {
  const input = document.querySelector("[data-command-input]");
  const list = document.querySelector("[role=listbox]");
  return {
    inputRole: input?.getAttribute("role"),
    controls: input?.getAttribute("aria-controls"),
    listId: list?.id,
    selected: document.querySelectorAll('[role="option"][aria-selected="true"]').length
  };
})()`);
assert(commandAccessibility.inputRole === "combobox", "Command palette input is missing combobox semantics.");
assert(commandAccessibility.controls === commandAccessibility.listId, "Command palette input is not linked to its result list.");
assert(commandAccessibility.selected === 1, "Command palette does not expose one selected result.");
await evaluate(`(() => {
  const input = document.querySelector("[data-command-input]");
  input.focus();
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
})()`);
assert(
  await evaluate(`document.querySelector('[role="option"][aria-selected="true"]')?.dataset.commandRoute === "projects"`),
  "ArrowDown did not move command palette selection.",
);
await evaluate(`document.querySelector("[data-command-input]").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }))`);
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
  destination.dispatchEvent(new DragEvent("dragleave", { bubbles: true, dataTransfer: transfer }));
  destination.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  destination.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  source.dispatchEvent(new DragEvent("dragend", { bubbles: true, dataTransfer: transfer }));
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

const toastActions = [
  ["login", "download-desktop", "Mesh Desktop"],
  ["projects", "create-project", "项目创建面板已就绪"],
  ["members", "invite-member", "邀请已复制"],
  ["agents", "create-agent", "智能体创建向导已打开"],
  ["skills", "import-skill", "Skill 导入向导已打开"],
  ["issue", "follow-issue", "已关注 MES-147"],
  ["squads", "filter-placeholder", "筛选菜单"],
  ["issues", "sort-issues", "已反转更新时间排序"],
  ["autopilot", "automation-runs", "运行记录"],
  ["autopilot", "create-automation", "自动化创建向导已打开"],
  ["usage", "export-data", "导出任务已创建"],
  ["inbox", "mark-read", "收件箱已清空未读"],
  ["chat", "new-chat", "已创建新对话"],
  ["settings", "save-settings", "设置已保存"],
  ["agent", "edit-agent", "智能体编辑面板已打开"],
  ["skill", "edit-skill", "Skill 编辑器已打开"],
  ["skill", "bind-skill", "已打开智能体选择器"],
];
for (const [route, action, expected] of toastActions) {
  await evaluate(`location.hash = ${JSON.stringify(`#/${route}`)}`);
  await evaluate(`new Promise(resolve => setTimeout(resolve, 35))`);
  await evaluate(`document.querySelector("#toast-region").replaceChildren()`);
  await evaluate(`document.querySelector(${JSON.stringify(`[data-action="${action}"]`)}).click()`);
  assert(
    await evaluate(`document.querySelector("#toast-region").textContent.includes(${JSON.stringify(expected)})`),
    `Action ${action} did not provide its expected feedback.`,
  );
}

await evaluate(`location.hash = "#/inbox"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector('[data-action="select-inbox"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector("#toast-region").replaceChildren()`);
await evaluate(`document.querySelector('[data-action="archive-notification"]').click()`);
assert(await evaluate(`document.querySelector("#toast-region").textContent.includes("通知已归档")`), "Inbox archive action did not provide feedback.");
await evaluate(`document.querySelector('[data-action="open-selected-issue"]').click()`);
assert(await evaluate(`location.hash === "#/issue"`), "Inbox notification did not open its Issue.");

await evaluate(`location.hash = "#/autopilot"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
const automationBefore = await evaluate(`document.querySelector('[data-action="toggle-automation"]').getAttribute("aria-checked")`);
await evaluate(`document.querySelector('[data-action="toggle-automation"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
const automationAfter = await evaluate(`document.querySelector('[data-action="toggle-automation"]').getAttribute("aria-checked")`);
assert(automationBefore !== automationAfter, "Autopilot enable switch did not change state.");

await evaluate(`location.hash = "#/settings"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector('[data-tab="shortcuts"]').click()`);
assert(await evaluate(`document.querySelector(".settings-section h1").textContent === "Shortcuts"`), "Settings subsection did not render.");
await evaluate(`document.querySelector('[data-tab="preferences"]').click()`);
const preferenceBefore = await evaluate(`document.querySelector('[data-action="toggle-switch"]').getAttribute("aria-checked")`);
await evaluate(`document.querySelector('[data-action="toggle-switch"]').click()`);
const preferenceAfter = await evaluate(`document.querySelector('[data-action="toggle-switch"]').getAttribute("aria-checked")`);
assert(preferenceBefore !== preferenceAfter, "Preference switch did not change state.");

await evaluate(`location.hash = "#/agent"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector('[data-action="go-chat"]').click()`);
assert(await evaluate(`location.hash === "#/chat"`), "Agent chat action did not open Chat.");
await evaluate(`location.hash = "#/agent"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector('[data-action="go-skills"]').click()`);
assert(await evaluate(`location.hash === "#/skills"`), "Agent skill action did not open Skills.");

await evaluate(`location.hash = "#/project"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector('[data-action="project-settings"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(await evaluate(`location.hash === "#/settings" && document.querySelector(".settings-section h1").textContent === "General"`), "Project settings action did not open workspace settings.");

await evaluate(`location.hash = "#/states"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector("#toast-region").replaceChildren()`);
await evaluate(`document.querySelector('[data-action="retry-state"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 760))`);
assert(await evaluate(`document.querySelector("#toast-region").textContent.includes("连接已恢复")`), "Error-state retry did not recover.");

await evaluate(`location.hash = "#/issues"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector('[data-action="open-workspaces"]').click()`);
assert(await evaluate(`Boolean(document.querySelector('[data-action="select-workspace"]'))`), "Workspace switcher did not open.");
await evaluate(`document.querySelector('[data-action="select-workspace"][data-code="PL"]').click()`);
assert(await evaluate(`document.querySelector(".workspace-trigger__name").textContent === "产品实验室"`), "Workspace switch did not update the shell.");
await evaluate(`document.querySelector('[data-action="open-workspaces"]').click()`);
await evaluate(`document.querySelector('[data-action="create-workspace"]').click()`);
await evaluate(`(() => {
  const form = document.querySelector('[data-form="create-workspace"]');
  form.elements.name.value = "视觉验证组";
  form.elements.code.value = "VV";
  form.requestSubmit();
})()`);
assert(await evaluate(`document.querySelector(".workspace-trigger__name").textContent === "视觉验证组"`), "Workspace creation did not update the shell.");
await evaluate(`document.querySelector('[data-action="open-workspaces"]').click()`);
await evaluate(`document.querySelector('[data-action="workspace-settings"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(await evaluate(`location.hash === "#/settings" && document.querySelector(".settings-section h1").textContent === "General"`), "Workspace settings action did not open General settings.");
checks.push("page-actions");

await evaluate(`location.hash = "#/issue"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
await evaluate(`document.querySelector("#toast-region").replaceChildren()`);
const commentSubmission = await evaluate(`(() => {
  const form = document.querySelector('[data-form="comment"]');
  form.elements.comment.value = "已完成真实浏览器评论验证";
  form.requestSubmit();
  return {
    reset: form.elements.comment.value === "",
    feedback: document.querySelector("#toast-region").textContent
  };
})()`);
assert(commentSubmission.reset, "Issue comment form did not reset after submission.");
assert(commentSubmission.feedback.includes("评论已发布"), "Issue comment submission did not provide success feedback.");
checks.push("issue-comment");

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
  menuButton: getComputedStyle(document.querySelector(".mobile-menu-button")).display,
  documentWidth: document.documentElement.scrollWidth,
  viewportWidth: window.innerWidth,
  toolbarHeight: document.querySelector(".workspace-toolbar--issues").getBoundingClientRect().height,
  boardColumnWidth: document.querySelector(".board-column").getBoundingClientRect().width
})`);
assert(mobileLayout.bottomNav === "grid", "Mobile bottom navigation is not visible.");
assert(mobileLayout.menuButton !== "none", "Mobile menu trigger is not visible.");
assert(mobileLayout.documentWidth <= mobileLayout.viewportWidth, "Mobile issue board causes page-level horizontal overflow.");
assert(mobileLayout.toolbarHeight >= 88, "Mobile issue controls are compressed into an overlapping toolbar.");
assert(mobileLayout.boardColumnWidth >= mobileLayout.viewportWidth - 32, "Mobile issue board does not present one readable lane at a time.");
await evaluate(`document.querySelector('[data-action="mobile-menu"]').click()`);
assert(await evaluate(`Boolean(document.querySelector(".mobile-drawer"))`), "Mobile drawer did not open.");
await evaluate(`document.querySelector(".drawer-backdrop").click()`);

await evaluate(`location.hash = "#/chat"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 60))`);
assert(await evaluate(`Boolean(document.querySelector('[data-action="back-to-chat-list"]'))`), "Mobile chat does not offer a route back to conversations.");
await evaluate(`document.querySelector('[data-action="back-to-chat-list"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(
  await evaluate(`getComputedStyle(document.querySelector(".chat-sessions")).display !== "none"`),
  "Mobile chat conversation list is unreachable.",
);
await evaluate(`document.querySelector('[data-action="select-chat"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(
  await evaluate(`getComputedStyle(document.querySelector(".chat-room")).display !== "none"`),
  "Mobile chat did not open the selected conversation.",
);

await evaluate(`location.hash = "#/inbox"`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 60))`);
await evaluate(`document.querySelector('[data-action="select-inbox"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
const mobileInbox = await evaluate(`({
  listDisplay: getComputedStyle(document.querySelector(".split-list")).display,
  detailDisplay: getComputedStyle(document.querySelector(".split-detail")).display,
  hasBack: Boolean(document.querySelector('[data-action="back-to-inbox-list"]'))
})`);
assert(mobileInbox.listDisplay === "none", "Mobile inbox keeps the list over the selected notification.");
assert(mobileInbox.detailDisplay !== "none", "Mobile inbox notification detail is unreachable.");
assert(mobileInbox.hasBack, "Mobile inbox does not offer a route back to notifications.");
await evaluate(`document.querySelector('[data-action="back-to-inbox-list"]').click()`);
await evaluate(`new Promise(resolve => setTimeout(resolve, 40))`);
assert(
  await evaluate(`getComputedStyle(document.querySelector(".split-list")).display !== "none"`),
  "Mobile inbox did not return to the notification list.",
);
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

const interactionCoverage = await command("Profiler.takePreciseCoverage");
await command("Page.reload", { ignoreCache: true });
let cleanVisualState = false;
for (let attempt = 0; attempt < 20; attempt += 1) {
  await new Promise((resolve) => setTimeout(resolve, 50));
  try {
    cleanVisualState = await evaluate(`document.readyState === "complete" && Boolean(document.querySelector("#prototype-root")?.children.length)`);
  } catch {
    cleanVisualState = false;
  }
  if (cleanVisualState) break;
}
assert(cleanVisualState, "The prototype did not reload into a clean visual-test state.");

const visualRoutes = ["login", "register", "code", ...routes];
const visualViewports = [
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "mobile", width: 390, height: 844, mobile: true },
];
for (const viewport of visualViewports) {
  await command("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: 1,
    mobile: viewport.mobile,
  });
  for (const theme of ["light", "dark"]) {
    for (const route of visualRoutes) {
      await evaluate(`(() => {
        document.documentElement.dataset.theme = ${JSON.stringify(theme)};
        location.hash = ${JSON.stringify(`#/${route}`)};
      })()`);
      await evaluate(`new Promise(resolve => setTimeout(resolve, 35))`);
      const layout = await evaluate(`(() => {
        const root = document.querySelector("#prototype-root");
        const isAuth = Boolean(document.querySelector(".auth-screen"));
        const page = document.querySelector(".page-layout");
        const mobileTabs = document.querySelector(".mobile-tabs");
        const sidebar = document.querySelector(".side-panel");
        return {
          hasExpectedRoot: isAuth || Boolean(page),
          bodyTextLength: document.body.textContent.trim().length,
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: window.innerWidth,
          rootWidth: Math.ceil(root.getBoundingClientRect().width),
          theme: document.documentElement.dataset.theme,
          mobileTabsDisplay: mobileTabs ? getComputedStyle(mobileTabs).display : "absent",
          sidebarDisplay: sidebar ? getComputedStyle(sidebar).display : "absent",
          isAuth
        };
      })()`);
      assert(layout.hasExpectedRoot, `${viewport.name}/${theme}/${route} did not render a page root.`);
      assert(layout.bodyTextLength > 20, `${viewport.name}/${theme}/${route} rendered no meaningful content.`);
      assert(layout.documentWidth <= layout.viewportWidth, `${viewport.name}/${theme}/${route} causes page-level horizontal overflow.`);
      assert(layout.rootWidth <= layout.viewportWidth, `${viewport.name}/${theme}/${route} exceeds the viewport root width.`);
      assert(layout.theme === theme, `${viewport.name}/${theme}/${route} did not retain the requested theme.`);
      if (!layout.isAuth && viewport.mobile) {
        assert(layout.mobileTabsDisplay === "grid", `mobile/${theme}/${route} lost the bottom navigation.`);
        assert(layout.sidebarDisplay === "none", `mobile/${theme}/${route} did not collapse the desktop sidebar.`);
      }
      if (!layout.isAuth && !viewport.mobile) {
        assert(layout.sidebarDisplay !== "none", `desktop/${theme}/${route} lost the workspace sidebar.`);
      }
      if (captureVisuals) {
        const screenshot = await command("Page.captureScreenshot", {
          format: "png",
          fromSurface: true,
          captureBeyondViewport: false,
        });
        const filename = `${viewport.name}-${theme}-${route}.png`;
        await writeFile(new URL(filename, visualOutput), Buffer.from(screenshot.data, "base64"));
        visualArtifactCount += 1;
      }
    }
  }
}
await command("Emulation.clearDeviceMetricsOverride");
assert(!captureVisuals || visualArtifactCount === 96, "Visual regression capture did not produce all 96 page combinations.");
checks.push("four-mode-page-matrix");

const preciseCoverage = await command("Profiler.takePreciseCoverage");
const appScriptUrl = new URL("./app.js", import.meta.url).href;
const appCoverageEntries = [...interactionCoverage.result, ...preciseCoverage.result].filter((entry) => entry.url === appScriptUrl);
assert(appCoverageEntries.length > 0, "V8 did not return coverage for app.js.");
const functionMap = new Map();
for (const entry of appCoverageEntries) {
  for (const fn of entry.functions.filter((candidate) => candidate.ranges.length > 0)) {
    const rootRange = fn.ranges[0];
    const key = `${fn.functionName}:${rootRange.startOffset}:${rootRange.endOffset}`;
    const previous = functionMap.get(key);
    if (!previous || rootRange.count > previous.ranges[0].count) functionMap.set(key, fn);
  }
}
const measuredFunctions = [...functionMap.values()];
const coveredFunctions = measuredFunctions.filter((fn) => fn.ranges[0].count > 0);
const functionCoverage = Number(((coveredFunctions.length / measuredFunctions.length) * 100).toFixed(2));
const appSource = await readFile(new URL("./app.js", import.meta.url), "utf8");

const coverageSnapshots = appCoverageEntries.map((entry) => entry.functions.flatMap((fn) => fn.ranges));
const boundaries = [...new Set(coverageSnapshots.flatMap((ranges) => ranges.flatMap((range) => [range.startOffset, range.endOffset])))].sort((a, b) => a - b);
const coverageSegments = boundaries.slice(0, -1).map((startOffset, index) => {
  const endOffset = boundaries[index + 1];
  const covered = coverageSnapshots.some((ranges) => {
    let mostSpecific = null;
    for (const range of ranges) {
      if (range.startOffset > startOffset || range.endOffset < endOffset) continue;
      const length = range.endOffset - range.startOffset;
      if (!mostSpecific || length < mostSpecific.length || (length === mostSpecific.length && range.count < mostSpecific.count)) {
        mostSpecific = { count: range.count, length };
      }
    }
    return Boolean(mostSpecific?.count);
  });
  return { startOffset, endOffset, covered };
});
const coveredBytes = coverageSegments
  .filter((segment) => segment.covered)
  .reduce((total, segment) => total + segment.endOffset - segment.startOffset, 0);
const measuredBytes = coverageSegments.reduce((total, segment) => total + segment.endOffset - segment.startOffset, 0);
const byteCoverage = Number(((coveredBytes / measuredBytes) * 100).toFixed(2));

let sourceOffset = 0;
const measuredLines = appSource.split("\n").flatMap((line, index) => {
  const trimmedStart = line.search(/\S/);
  const lineStart = sourceOffset;
  sourceOffset += line.length + 1;
  if (trimmedStart === -1) return [];
  const contentStart = lineStart + trimmedStart;
  const contentEnd = lineStart + line.length;
  const covered = coverageSegments.some(
    (segment) => segment.covered && segment.endOffset > contentStart && segment.startOffset < contentEnd,
  );
  return [{ line: index + 1, covered }];
});
const coveredLines = measuredLines.filter((line) => line.covered).length;
const lineCoverage = Number(((coveredLines / measuredLines.length) * 100).toFixed(2));

if (functionCoverage < 90 || lineCoverage < 90 || byteCoverage < 90) {
  const lineAt = (offset) => appSource.slice(0, offset).split("\n").length;
  const uncovered = measuredFunctions
    .filter((fn) => fn.ranges[0].count === 0)
    .map((fn) => ({ name: fn.functionName || "(anonymous)", line: lineAt(fn.ranges[0].startOffset) }));
  console.error(JSON.stringify({ functionCoverage, lineCoverage, byteCoverage, uncovered }, null, 2));
}
assert(functionCoverage >= 90, `app.js function coverage is ${functionCoverage}%; expected at least 90%.`);
assert(lineCoverage >= 90, `app.js line coverage is ${lineCoverage}%; expected at least 90%.`);
assert(byteCoverage >= 90, `app.js byte coverage is ${byteCoverage}%; expected at least 90%.`);
await command("Profiler.stopPreciseCoverage");
await command("Profiler.disable");
checks.push("v8-coverage");

console.log(JSON.stringify({ ok: true, checks, functionCoverage, lineCoverage, byteCoverage, visualArtifacts: visualArtifactCount }, null, 2));
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
