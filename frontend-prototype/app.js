(() => {
  "use strict";

  const root = document.querySelector("#prototype-root");
  const overlayRoot = document.querySelector("#overlay-root");
  const toastRegion = document.querySelector("#toast-region");

  const icons = {
    search:
      '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.4 15.4 4.1 4.1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    inbox:
      '<path d="M4 5.5h16v13H4z"/><path d="M4 14h4l1.5 2h5L16 14h4"/>',
    chat:
      '<path d="M4 5.5h16v11H9l-5 3v-14Z"/><path d="M8 10h8M8 13h5"/>',
    my: '<circle cx="12" cy="8" r="3.5"/><path d="M5.5 20c.5-4 2.7-6 6.5-6s6 2 6.5 6"/>',
    issues:
      '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 10 1.5 1.5L12 8.5M14 10h3M8 16h9"/>',
    board:
      '<rect x="3.5" y="4" width="7.5" height="16" rx="2"/><rect x="13" y="4" width="7.5" height="10" rx="2"/>',
    project:
      '<path d="M3.5 7.5h6l2-2H20.5v13H3.5z"/><path d="M3.5 10h17"/>',
    members:
      '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3.5 20c.3-4 2.2-6 5.5-6s5.2 2 5.5 6M14.5 15c3.6-.4 5.5 1.3 6 4.5"/>',
    agent:
      '<rect x="4" y="6" width="16" height="13" rx="4"/><path d="M12 3v3M8.5 12h.1M15.5 12h.1M8 16h8"/>',
    skill:
      '<path d="M12 3.5 14 8l4.5 2-4.5 2-2 4.5-2-4.5-4.5-2L10 8z"/><path d="m18 15 .8 1.8L21 18l-2.2 1.2L18 21l-.8-1.8L15 18l2.2-1.2z"/>',
    zap: '<path d="m13.5 2.5-8 11h6l-1 8 8-11h-6z"/>',
    chart:
      '<path d="M4 20V10M10 20V5M16 20v-7M22 20H2"/><path d="m4 8 6-4 6 6 5-5"/>',
    runtime:
      '<rect x="3.5" y="5" width="17" height="14" rx="3"/><path d="m7 10 3 2-3 2M13 15h4"/>',
    settings:
      '<circle cx="12" cy="12" r="3"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/>',
    state:
      '<circle cx="12" cy="12" r="9"/><path d="M8 9h8M8 12h5M8 15h7"/>',
    command:
      '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
    moon:
      '<path d="M20 15.5A8.5 8.5 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z"/>',
    sun:
      '<circle cx="12" cy="12" r="3.5"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    filter:
      '<path d="M4 5h16l-6.5 7v5l-3 2v-7z"/>',
    sort: '<path d="M8 5v14m0 0-3-3m3 3 3-3M16 19V5m0 0-3 3m3-3 3 3"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    calendar:
      '<rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M7 3v4M17 3v4M3.5 9h17"/>',
    link: '<path d="M10 14 8.5 15.5a3.5 3.5 0 0 1-5-5L7 7a3.5 3.5 0 0 1 5 0M14 10l1.5-1.5a3.5 3.5 0 1 1 5 5L17 17a3.5 3.5 0 0 1-5 0"/>',
    attach:
      '<path d="m8 12 5.5-5.5a3 3 0 0 1 4.2 4.2l-7.2 7.2a5 5 0 0 1-7.1-7.1l7.3-7.3"/><path d="m9 15 6.5-6.5"/>',
    send: '<path d="m3 11 18-8-8 18-2-8zM11 13l10-10"/>',
    edit:
      '<path d="M5 19h4L19 9l-4-4L5 15zM13.5 6.5l4 4"/>',
    arrow: '<path d="M5 12h14M14 7l5 5-5 5"/>',
    home: '<path d="m3 11 9-7 9 7v9h-6v-6H9v6H3z"/>',
    bell:
      '<path d="M6 16.5h12l-1.5-2V10a4.5 4.5 0 0 0-9 0v4.5zM10 19.5h4"/>',
    shield: '<path d="M12 3 20 6v5c0 5-3.5 8.3-8 10-4.5-1.7-8-5-8-10V6z"/><path d="m8.5 12 2 2 5-5"/>',
    palette:
      '<path d="M12 3a9 9 0 1 0 0 18h1.2a2 2 0 0 0 1.2-3.6 2 2 0 0 1 1.2-3.6H18a3 3 0 0 0 3-3C21 6.5 17 3 12 3Z"/><circle cx="7.5" cy="10" r=".8"/><circle cx="10" cy="6.5" r=".8"/><circle cx="15" cy="7" r=".8"/>',
    logout: '<path d="M10 4H5v16h5M14 8l4 4-4 4M18 12H9"/>',
    mail:
      '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/>',
    alert:
      '<path d="M12 3 2.8 20h18.4z"/><path d="M12 9v5M12 17.5h.01"/>',
    refresh:
      '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M18 9a7 7 0 0 0-12-2M6 15a7 7 0 0 0 12 2"/>',
    grip: '<circle cx="9" cy="7" r=".9"/><circle cx="15" cy="7" r=".9"/><circle cx="9" cy="12" r=".9"/><circle cx="15" cy="12" r=".9"/><circle cx="9" cy="17" r=".9"/><circle cx="15" cy="17" r=".9"/>',
    copy: '<rect x="8" y="8" width="11" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/>',
  };

  const icon = (name, modifier = "") =>
    `<svg class="icon ${modifier}" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.state}</svg>`;

  const escapeHtml = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const people = {
    you: { initials: "闻", tone: "", name: "陈闻峰" },
    lin: { initials: "林", tone: "avatar--green", name: "林澄" },
    zhao: { initials: "赵", tone: "avatar--orange", name: "赵可" },
    qiao: { initials: "乔", tone: "avatar--violet", name: "乔远" },
    agent: { initials: "M", tone: "avatar--agent", name: "Mesh 工程师" },
    design: { initials: "D", tone: "avatar--agent", name: "设计助手" },
  };

  const avatar = (key, large = false) => {
    const person = people[key] || people.you;
    return `<span class="avatar ${person.tone} ${large ? "avatar--large" : ""}" title="${person.name}">${person.initials}</span>`;
  };

  const state = {
    route: "",
    workspace: "Mesh",
    workspaceCode: "ME",
    inboxSelected: -1,
    chatSelected: -1,
    settingsTab: "profile",
    commandQuery: "",
    issueSequence: 148,
    authEmail: "",
    messages: [
      {
        from: "agent",
        text: "我已梳理完本轮迭代。当前 3 个高优先级 Issue 中，登录回跳修复已经进入评审；看板性能优化仍缺一组移动端数据。",
        time: "14:18",
      },
      {
        from: "me",
        text: "先把移动端数据补齐，再检查一下暗色主题的对比度。完成后把结论更新到 Issue。",
        time: "14:20",
      },
      {
        from: "agent",
        text: "收到。我会先完成 390px 视口走查，再用语义色板复核文本、边界与状态色，最后回写可复现的结论。",
        time: "14:20",
      },
    ],
    issues: [
      {
        key: "MES-147",
        title: "移动端看板横向滚动时保持列标题可见",
        status: "progress",
        statusText: "进行中",
        priority: "urgent",
        assignee: "agent",
        project: "移动体验",
        updated: "3 分钟前",
        label: "体验",
        labelTone: "",
      },
      {
        key: "MES-146",
        title: "登录成功后恢复用户原始访问路径",
        status: "review",
        statusText: "待评审",
        priority: "high",
        assignee: "you",
        project: "身份与权限",
        updated: "21 分钟前",
        label: "安全",
        labelTone: "label--violet",
      },
      {
        key: "MES-145",
        title: "项目概览补充里程碑健康度",
        status: "todo",
        statusText: "待办",
        priority: "normal",
        assignee: "lin",
        project: "项目管理",
        updated: "1 小时前",
        label: "增强",
        labelTone: "label--green",
      },
      {
        key: "MES-144",
        title: "运行时离线后显示重连与数据恢复进度",
        status: "progress",
        statusText: "进行中",
        priority: "high",
        assignee: "agent",
        project: "执行平台",
        updated: "2 小时前",
        label: "可靠性",
        labelTone: "label--orange",
      },
      {
        key: "MES-143",
        title: "统一评论编辑器中的附件预览尺寸",
        status: "done",
        statusText: "已完成",
        priority: "normal",
        assignee: "zhao",
        project: "协作体验",
        updated: "昨天",
        label: "界面",
        labelTone: "",
      },
      {
        key: "MES-141",
        title: "命令面板支持最近访问与模糊匹配",
        status: "todo",
        statusText: "待办",
        priority: "normal",
        assignee: "qiao",
        project: "效率工具",
        updated: "昨天",
        label: "效率",
        labelTone: "label--violet",
      },
    ],
    board: {
      backlog: [],
      todo: [],
      progress: [],
      review: [],
      done: [],
    },
    automations: [
      { name: "工作日站会摘要", detail: "汇总昨日进展与今日阻塞", schedule: "工作日 09:30", target: "项目动态", run: "今天 09:30", enabled: true },
      { name: "高优先级 Issue 巡检", detail: "检查超时、阻塞与无人负责事项", schedule: "每 2 小时", target: "Issue", run: "48 分钟前", enabled: true },
      { name: "周度用量报告", detail: "按项目和智能体聚合成本与时长", schedule: "周一 10:00", target: "分析", run: "本周一", enabled: true },
      { name: "过期里程碑提醒", detail: "通知项目负责人更新健康状态", schedule: "每天 18:00", target: "收件箱", run: "昨天 18:00", enabled: false },
    ],
  };

  const routeInfo = {
    inbox: { label: "Inbox", icon: "inbox" },
    chat: { label: "Chat", icon: "chat" },
    my: { label: "My Issues", icon: "my" },
    issues: { label: "Issues", icon: "issues" },
    board: { label: "Issues", icon: "board" },
    issue: { label: "MES-147", icon: "issues" },
    projects: { label: "Projects", icon: "project" },
    project: { label: "Mesh Web", icon: "project" },
    members: { label: "Members", icon: "members" },
    agents: { label: "Agents", icon: "agent" },
    agent: { label: "Mesh 工程师", icon: "agent" },
    skills: { label: "Skills", icon: "skill" },
    skill: { label: "界面评审", icon: "skill" },
    autopilot: { label: "Autopilot", icon: "zap" },
    automations: { label: "Autopilot", icon: "zap" },
    squads: { label: "Squads", icon: "members" },
    runtimes: { label: "Runtimes", icon: "runtime" },
    usage: { label: "Usage", icon: "chart" },
    analytics: { label: "Usage", icon: "chart" },
    settings: { label: "Settings", icon: "settings" },
    states: { label: "状态画廊", icon: "state" },
  };

  const navGroups = [
    {
      title: "",
      items: [
        ["inbox", "Inbox", "inbox", "17"],
        ["chat", "Chat", "chat", "5"],
        ["my", "My Issues", "my", ""],
      ],
    },
    {
      title: "Workspace",
      items: [
        ["issues", "Issues", "issues", ""],
        ["projects", "Projects", "project", ""],
        ["autopilot", "Autopilot", "zap", ""],
        ["agents", "Agents", "agent", ""],
        ["squads", "Squads", "members", ""],
        ["usage", "Usage", "chart", ""],
      ],
    },
    {
      title: "Configure",
      items: [
        ["runtimes", "Runtimes", "runtime", ""],
        ["skills", "Skills", "skill", ""],
        ["settings", "Settings", "settings", ""],
      ],
    },
  ];

  const priority = (level) =>
    `<span class="priority-mark priority-mark--${level}" title="优先级"><i></i><i></i><i></i></span>`;

  const statusPill = (status, label) =>
    `<span class="status-pill status-pill--${status}">${label}</span>`;

  const button = (label, iconName, action, variant = "outline", extra = "") =>
    `<button class="ui-button ui-button--small ui-button--${variant} ${extra}" type="button" data-action="${action}">
      ${iconName ? icon(iconName, "icon--sm") : ""}<span>${label}</span>
    </button>`;

  function currentRoute() {
    const route = location.hash.replace(/^#\/?/, "").split("?")[0].trim();
    return route || "issues";
  }

  function setRoute(route) {
    if (currentRoute() === route) {
      state.route = route;
      render();
      return;
    }
    location.hash = `#/${route}`;
  }

  function navItem([route, label, iconName, badge]) {
    const active = state.route === route || (route === "issues" && state.route === "issue") ||
      (route === "projects" && state.route === "project") ||
      (route === "agents" && state.route === "agent") ||
      (route === "skills" && state.route === "skill");
    return `<button class="nav-link ${active ? "is-active" : ""}" type="button" data-route="${route}">
      ${icon(iconName)}
      <span class="nav-link__label">${label}</span>
      ${badge ? `<span class="nav-link__badge">${badge}</span>` : ""}
    </button>`;
  }

  function sidebar(innerClass = "") {
    return `<aside class="side-panel ${innerClass}">
      <div class="side-panel__head">
        <button class="workspace-trigger" type="button" data-action="open-workspaces">
          <span class="mesh-workspace-mark" aria-hidden="true"><i></i></span>
          <span class="workspace-trigger__copy">
            <span class="workspace-trigger__name">${escapeHtml(state.workspace)}</span>
          </span>
          ${icon("down", "icon--sm")}
        </button>
      </div>
      <button class="side-search" type="button" data-action="open-command">
        ${icon("search", "icon--sm")}<span>Search...</span><kbd class="shortcut">Ctrl K</kbd>
      </button>
      <button class="side-create" type="button" data-action="open-create-issue">
        ${icon("edit", "icon--sm")}<span>New Issue</span><kbd class="shortcut">C</kbd>
      </button>
      <div class="side-panel__scroll">
        ${navGroups
          .map(
            (group) => `<section class="nav-block">
              ${group.title ? `<div class="nav-block__title">${group.title}</div>` : ""}
              ${group.items.map(navItem).join("")}
            </section>`,
          )
          .join("")}
      </div>
      <div class="side-panel__foot">
        <aside class="community-note">
          <span class="community-note__glyph">M</span>
          <span><strong>Join the Mesh community</strong><small>Chat with teammates and builders.</small></span>
          <button type="button" aria-label="Dismiss">${icon("close", "icon--sm")}</button>
        </aside>
        <button class="help-trigger" type="button" data-action="help" aria-label="Help">?</button>
      </div>
    </aside>`;
  }

  function pageBar(title, iconName, options = {}) {
    return `<header class="page-bar">
      <button class="ui-button ui-button--icon mobile-menu-button" type="button" data-action="mobile-menu" aria-label="打开导航">${icon("menu")}</button>
      <div class="page-bar__title">${title}</div>
      ${options.count ? `<span class="page-bar__count">${options.count}</span>` : ""}
      <div class="page-bar__actions">
        ${options.actions || ""}
      </div>
    </header>`;
  }

  function mobileTabs() {
    const items = [
      ["inbox", "Inbox", "inbox"],
      ["issues", "Issues", "issues"],
      ["projects", "Projects", "project"],
      ["chat", "Chat", "chat"],
      ["settings", "More", "menu"],
    ];
    return `<nav class="mobile-tabs" aria-label="移动端导航">
      ${items
        .map(
          ([route, label, iconName]) =>
            `<button class="mobile-tab ${state.route === route ? "is-active" : ""}" type="button" data-route="${route}">${icon(iconName)}<span>${label}</span></button>`,
        )
        .join("")}
    </nav>`;
  }

  function appShell(page) {
    return `<div class="workspace-app">
      ${sidebar()}
      <main class="stage-frame">${page}<button class="chat-fab" type="button" data-route="chat" aria-label="打开聊天">${icon("chat", "icon--lg")}</button></main>
      ${mobileTabs()}
    </div>`;
  }

  function authPage(mode) {
    const isRegister = mode === "register";
    const isCode = mode === "code";
    const title = isCode ? "Check your email" : isRegister ? "Create your Mesh account" : "Sign in to Mesh";
    const description = isCode
      ? `We sent a verification code to ${escapeHtml(state.authEmail || "you@example.com")}`
      : isRegister
        ? "Create an account to start working with your AI teammates"
        : "Enter your email to get a login code";

    const form = isCode
      ? `<form class="modal-form" data-form="verify-code">
          <div class="otp-row">
            ${Array.from({ length: 6 }, (_, index) => `<input class="otp-cell" inputmode="numeric" maxlength="1" pattern="[0-9]" required aria-label="Verification code digit ${index + 1}" ${index === 0 ? "autofocus" : ""}/>`).join("")}
          </div>
          <button class="ui-button ui-button--primary" type="submit" data-auth-submit disabled>Continue</button>
        </form>`
      : `<form class="modal-form" data-form="${isRegister ? "register" : "login"}">
          ${isRegister ? `<div class="form-field"><label class="form-label" for="name">Name</label><input id="name" class="ui-input" name="name" placeholder="Your name" autocomplete="name" required /></div>` : ""}
          <div class="form-field">
            <label class="form-label" for="email">Email</label>
            <input id="email" class="ui-input" name="email" type="email" placeholder="you@example.com" autocomplete="email" required autofocus />
          </div>
          <button class="ui-button ui-button--primary" type="submit" data-auth-submit disabled>${isRegister ? "Create account" : "Continue"}</button>
        </form>`;

    return `<main class="auth-screen">
      <div class="auth-shell">
        <section class="auth-card">
          <div class="auth-card__body">
            <div class="auth-card__heading"><h1>${title}</h1><p>${description}</p></div>
            ${form}
          </div>
          <div class="auth-card__footer">
            ${
              isCode
                ? `<button class="auth-switch" type="button" data-route="login">Back</button>`
                : isRegister
                  ? `<button class="auth-switch" type="button" data-route="login">Already have an account? <strong>Sign in</strong></button>`
                  : `<button class="auth-switch" type="button" data-action="download-desktop">Prefer the desktop app? <strong>Download</strong></button>`
            }
          </div>
        </section>
      </div>
    </main>`;
  }

  function issuesPage() {
    const columns = [
      ["backlog", "Backlog", "board-column--backlog"],
      ["todo", "Todo", "board-column--todo"],
      ["progress", "In Progress", "board-column--progress"],
      ["review", "In Review", "board-column--review"],
    ];
    return `<div class="page-layout">
      ${pageBar("Issues")}
      <div class="workspace-toolbar workspace-toolbar--issues">
        <div class="workspace-tabs" role="tablist">
          <button class="is-active" type="button">All</button>
          <button type="button">Members</button>
          <button type="button">Agents</button>
        </div>
        <span class="u-spacer"></span>
        <button class="quiet-action working-count" type="button"><span class="presence-dot"></span><strong>0</strong><small>working</small></button>
        ${button("Filter", "filter", "filter-placeholder")}
        ${button("Manual", "sort", "filter-placeholder")}
        ${button("Board", "board", "filter-placeholder")}
      </div>
      <div class="board-viewport board-viewport--workspace">
        <div class="board board--workspace">
          ${columns
            .map(
              ([key, label, tone]) => `<section class="board-column ${tone}" data-drop-column="${key}">
                <header class="board-column__head"><span>${label}</span><span class="board-column__count">${state.board[key].length}</span></header>
                <div class="board-stack">
                  ${
                    state.board[key].length
                      ? state.board[key].map((card) => boardCard(card, key)).join("")
                      : `<div class="board-empty">No issues</div>`
                  }
                </div>
              </section>`,
            )
            .join("")}
        </div>
      </div>
    </div>`;
  }

  function myIssuesPage() {
    return `<div class="page-layout">
      ${pageBar("My Issues")}
      <div class="workspace-toolbar">
        <div class="workspace-tabs" role="tablist">
          <button class="is-active" type="button">All</button>
          <button type="button">Assigned</button>
          <button type="button">Created</button>
          <button type="button">My Agents and Squads</button>
        </div>
        <span class="u-spacer"></span>
        ${button("Filter", "filter", "filter-placeholder")}
        ${button("Manual", "sort", "filter-placeholder")}
        ${button("Board", "board", "filter-placeholder")}
      </div>
      <div class="workspace-empty"><p>No issues</p></div>
    </div>`;
  }

  function boardCard(card, column) {
    return `<article class="board-card" draggable="true" data-card-key="${card.key}" data-column="${column}" data-route="issue">
      <div class="board-card__key">${card.key}</div>
      <div class="board-card__title">${card.title}</div>
      <div class="board-card__meta">
        ${priority(card.priority)}
        <span class="label">${card.label}</span>
        ${avatar(card.person)}
      </div>
    </article>`;
  }

  function boardPage() {
    return issuesPage();
  }

  function issuePage() {
    return `<div class="page-layout">
      ${pageBar("MES-147", "issues", {
        actions: `${button("关注", "bell", "follow-issue")}${button("更多", "more", "filter-placeholder")}`,
      })}
      <div class="issue-detail-grid">
        <article class="issue-detail-main">
          <nav class="breadcrumb"><button class="auth-switch" data-route="issues">Issues</button>${icon("chevron", "icon--sm")}<span>MES-147</span></nav>
          <header class="issue-heading">
            <h1>移动端看板横向滚动时保持列标题可见</h1>
            <div class="issue-heading__meta">
              ${statusPill("progress", "进行中")}
              <span class="label">体验</span>
              <span class="u-muted u-mono">MES-147</span>
              <span class="u-muted">由 陈闻峰 创建 · 3 分钟前</span>
            </div>
          </header>
          <div class="prose">
            <p>在手机端浏览多列看板时，用户横向滚动后容易失去当前状态列的上下文。需要让列标题在纵向滚动时保持可见，同时不遮挡卡片拖动区域。</p>
            <ul>
              <li>覆盖 360px、390px 与 430px 三个常见视口。</li>
              <li>长标题不得挤压 WIP 数量与快捷添加按钮。</li>
              <li>亮暗主题下的悬浮层级和边界均需清晰。</li>
            </ul>
          </div>
          <section class="subtask-panel">
            <div class="section-heading">${icon("issues", "icon--sm")}子任务 <span class="u-mono u-muted">2 / 3</span><span class="u-spacer"></span>${button("添加", "plus", "filter-placeholder", "outline")}</div>
            <div class="surface-card">
              <div class="subtask-row">${icon("check", "u-success")}<span class="u-mono u-muted">MES-148</span><span>补充 390px 真实视口基线</span>${avatar("design")}</div>
              <div class="subtask-row">${icon("check", "u-success")}<span class="u-mono u-muted">MES-149</span><span>修正 sticky 标题的层级与背景</span>${avatar("agent")}</div>
              <div class="subtask-row">${icon("clock", "u-muted")}<span class="u-mono u-muted">MES-150</span><span>走查触控拖动与横向滚动冲突</span>${avatar("you")}</div>
            </div>
          </section>
          <section class="activity-panel">
            <div class="section-heading">${icon("chat", "icon--sm")}动态 <span class="u-mono u-muted">3</span></div>
            <div class="timeline">
              <div class="timeline-item">
                ${avatar("design")}
                <div class="comment">
                  <div class="comment__head"><strong>设计助手</strong><span class="u-muted">· 18 分钟前</span><span class="u-spacer"></span>${icon("more", "icon--sm")}</div>
                  <div class="comment__body">已完成三种手机宽度走查。390px 下第四列的 WIP 数量会被折行，建议标题容器改为单行网格，并让标题本身截断。</div>
                </div>
              </div>
              <div class="timeline-item">
                ${avatar("agent")}
                <div class="comment">
                  <div class="comment__head"><strong>Mesh 工程师</strong><span class="u-muted">· 7 分钟前</span><span class="u-spacer"></span>${icon("more", "icon--sm")}</div>
                  <div class="comment__body">修复已完成。列头在垂直滚动时固定，横向滚动仍跟随所属列；同时将触控拖动激活距离调整为 8px，避免误触。</div>
                </div>
              </div>
              <div class="timeline-item">
                ${avatar("you")}
                <form class="composer" data-form="comment">
                  <textarea class="ui-textarea" name="comment" placeholder="写下评论，使用 @ 提及队友…"></textarea>
                  <div class="composer__bar"><button class="ui-button ui-button--icon" type="button">${icon("attach")}</button><span class="u-spacer"></span><button class="ui-button ui-button--small ui-button--primary" type="submit">${icon("send", "icon--sm")}发布评论</button></div>
                </form>
              </div>
            </div>
          </section>
        </article>
        <aside class="issue-detail-side">
          <section class="property-group">
            <h2 class="property-group__title">属性</h2>
            <div class="property-row"><span class="property-row__label">状态</span><span class="property-row__value">${statusPill("progress", "进行中")}</span></div>
            <div class="property-row"><span class="property-row__label">负责人</span><span class="property-row__value">${avatar("agent")}Mesh 工程师</span></div>
            <div class="property-row"><span class="property-row__label">优先级</span><span class="property-row__value">${priority("urgent")}紧急</span></div>
            <div class="property-row"><span class="property-row__label">项目</span><span class="property-row__value">${icon("project", "icon--sm")}移动体验</span></div>
            <div class="property-row"><span class="property-row__label">截止日期</span><span class="property-row__value">${icon("calendar", "icon--sm")}8 月 2 日</span></div>
            <div class="property-row"><span class="property-row__label">标签</span><span class="property-row__value"><span class="label">体验</span><span class="label label--orange">移动端</span></span></div>
          </section>
          <section class="property-group">
            <h2 class="property-group__title">关系</h2>
            <div class="property-row"><span class="property-row__label">父 Issue</span><span class="property-row__value u-mono">MES-130</span></div>
            <div class="property-row"><span class="property-row__label">阻塞</span><span class="property-row__value">无</span></div>
            <div class="property-row"><span class="property-row__label">订阅者</span><span class="property-row__value"><span class="row-avatar-stack">${avatar("you")}${avatar("lin")}${avatar("agent")}</span></span></div>
          </section>
          <section class="property-group">
            <h2 class="property-group__title">活动</h2>
            <div class="property-row"><span class="property-row__label">创建</span><span class="property-row__value">今天 14:12</span></div>
            <div class="property-row"><span class="property-row__label">更新</span><span class="property-row__value">3 分钟前</span></div>
          </section>
        </aside>
      </div>
    </div>`;
  }

  function projectsPage() {
    const projects = [
      { name: "Mesh", code: "ME", detail: "AI-native workspace", progress: 68, tone: "", issues: 24, people: ["you", "agent", "design"] },
    ];
    return `<div class="page-layout">
      ${pageBar("Projects")}
      <div class="workspace-toolbar">
        <label class="search-field">${icon("search", "icon--sm")}<input class="ui-input" type="search" placeholder="Search projects..." /></label>
        ${button("Filter", "filter", "filter-placeholder")}
        ${button("Updated", "sort", "filter-placeholder")}
        <span class="u-spacer"></span>
        ${button("New project", "plus", "create-project", "outline")}
      </div>
      <div class="workspace-table-wrap">
        <table class="workspace-table">
          <thead><tr><th>Name</th><th>Lead</th><th>Progress</th><th>Issues</th><th>Updated</th><th></th></tr></thead>
          <tbody>
            ${projects.map((project) => `<tr data-route="project">
              <td><span class="project-tile">${project.code}</span><span><strong>${project.name}</strong><small>${project.detail}</small></span></td>
              <td>${avatar("you")}<span>陈闻峰</span></td>
              <td><span class="compact-progress"><i style="width:${project.progress}%"></i></span><span>${project.progress}%</span></td>
              <td>${project.issues}</td>
              <td>Today</td>
              <td>${icon("more", "icon--sm")}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
  }

  function projectPage() {
    return `<div class="page-layout">
      ${pageBar("Mesh Web", "project", { actions: `${button("项目设置", "settings", "project-settings")}${button("新建 Issue", "plus", "open-create-issue", "primary")}` })}
      <div class="page-scroll"><div class="page-content">
        <div class="page-intro">
          <div style="display:flex;gap:13px;align-items:center"><span class="avatar avatar--hero">MW</span><div class="page-intro__copy"><h1>Mesh Web</h1><p>桌面与移动端产品体验、设计系统和前端工程。</p></div></div>
          <span class="status-pill status-pill--progress">进展正常</span>
        </div>
        <div class="kpi-grid">
          <div class="kpi-card"><div class="kpi-card__label">完成度 ${icon("chart", "icon--sm")}</div><div class="kpi-card__value">68%</div><div class="progress-track"><i style="width:68%"></i></div></div>
          <div class="kpi-card"><div class="kpi-card__label">活跃 Issue ${icon("issues", "icon--sm")}</div><div class="kpi-card__value">24</div><div class="kpi-card__trend">过去 7 天 −6</div></div>
          <div class="kpi-card"><div class="kpi-card__label">里程碑 ${icon("calendar", "icon--sm")}</div><div class="kpi-card__value">3 / 5</div><div class="kpi-card__trend">下一个：移动体验</div></div>
          <div class="kpi-card"><div class="kpi-card__label">成员 ${icon("members", "icon--sm")}</div><div class="kpi-card__value">7</div><div class="row-avatar-stack">${avatar("you")}${avatar("agent")}${avatar("design")}${avatar("lin")}</div></div>
        </div>
        <div class="chart-grid">
          <section class="surface-card">
            <div class="surface-card__head"><h2>最近 Issues</h2><span class="u-spacer"></span><button class="auth-switch" data-route="issues">查看全部</button></div>
            ${state.issues.slice(0, 5).map((issue) => `<div class="issue-row" style="grid-template-columns:24px 62px minmax(0,1fr) 84px 42px">${priority(issue.priority)}<span class="u-mono u-muted">${issue.key}</span><span class="issue-row__title">${issue.title}</span>${statusPill(issue.status, issue.statusText)}${avatar(issue.assignee)}</div>`).join("")}
          </section>
          <section class="surface-card">
            <div class="surface-card__head"><h2>里程碑</h2><span class="u-spacer"></span>${icon("more", "icon--sm")}</div>
            <div class="surface-card__body" style="display:grid;gap:18px">
              <div><div style="display:flex;margin-bottom:7px"><strong style="font-size:12px">移动体验</strong><span class="u-spacer"></span><span class="u-muted" style="font-size:11px">8 月 2 日</span></div><div class="progress-track"><i style="width:76%"></i></div></div>
              <div><div style="display:flex;margin-bottom:7px"><strong style="font-size:12px">主题一致性</strong><span class="u-spacer"></span><span class="u-muted" style="font-size:11px">8 月 9 日</span></div><div class="progress-track"><i style="width:54%"></i></div></div>
              <div><div style="display:flex;margin-bottom:7px"><strong style="font-size:12px">性能收口</strong><span class="u-spacer"></span><span class="u-muted" style="font-size:11px">8 月 16 日</span></div><div class="progress-track"><i style="width:21%"></i></div></div>
            </div>
          </section>
        </div>
      </div></div>
    </div>`;
  }

  function membersPage() {
    const members = [
      ["you", "陈闻峰", "cnwenf@outlook.com", "所有者", "在线", "2026-05-18"],
      ["lin", "林澄", "lin@mesh.team", "管理员", "在线", "2026-05-21"],
      ["zhao", "赵可", "zhao@mesh.team", "成员", "2 小时前", "2026-06-03"],
      ["qiao", "乔远", "qiao@mesh.team", "成员", "昨天", "2026-06-12"],
      ["agent", "Mesh 工程师", "智能体 · 私有", "智能体", "正在工作", "2026-07-10"],
      ["design", "设计助手", "智能体 · 工作区", "智能体", "18 分钟前", "2026-07-14"],
    ];
    return `<div class="page-layout">
      ${pageBar("成员", "members", { count: members.length, actions: button("邀请成员", "plus", "invite-member", "primary") })}
      <div class="page-scroll"><div class="page-content">
        <div class="page-intro"><div class="page-intro__copy"><h1>成员名册</h1><p>人类与智能体共享同一份团队目录和协作身份。</p></div></div>
        <div class="toolbar surface-card" style="margin-bottom:12px;border-radius:10px">
          <div class="segmented"><button class="is-active">全部</button><button>成员</button><button>智能体</button><button>待加入</button></div>
          <span class="u-spacer"></span><label class="search-field">${icon("search", "icon--sm")}<input class="ui-input" placeholder="搜索成员…" /></label>
        </div>
        <section class="roster">
          ${members
            .map(
              ([person, name, email, role, active, joined]) => `<div class="roster-row">
                <div class="roster-person">${avatar(person, true)}<div class="roster-person__copy"><div class="roster-person__name">${name}</div><div class="roster-person__email">${email}</div></div></div>
                <div class="roster-row__role"><span class="meta-pill">${role}</span></div>
                <div class="${active.includes("在线") || active.includes("正在") ? "u-success" : "u-muted"}">${active}</div>
                <div class="roster-row__joined u-muted">${joined}</div>
                <button class="ui-button ui-button--icon" type="button">${icon("more", "icon--sm")}</button>
              </div>`,
            )
            .join("")}
        </section>
      </div></div>
    </div>`;
  }

  function agentsPage() {
    const agents = [
      ["agent", "Mesh 工程师", "Architecture and product engineering", "Claude Code", "Active now", "Private"],
      ["design", "Mesh 设计师", "Product design and interface review", "Codex", "18m ago", "Workspace"],
    ];
    return `<div class="page-layout">
      ${pageBar("Agents")}
      <div class="workspace-toolbar">
        <div class="workspace-tabs"><button class="is-active" type="button">Mine</button><button type="button">All</button><button type="button">Archived</button></div>
        <span class="u-spacer"></span>
        ${button("Filter", "filter", "filter-placeholder")}
        ${button("Updated", "sort", "filter-placeholder")}
        ${button("New agent", "plus", "create-agent", "outline")}
      </div>
      <div class="workspace-table-wrap">
        <table class="workspace-table">
          <thead><tr><th>Name</th><th>Runtime</th><th>Visibility</th><th>Last active</th><th></th></tr></thead>
          <tbody>${agents.map(([person, name, detail, runtime, active, visibility]) => `<tr data-route="agent">
            <td>${avatar(person)}<span><strong>${name}</strong><small>${detail}</small></span></td>
            <td>${runtime}</td><td>${visibility}</td><td>${active}</td><td>${icon("more", "icon--sm")}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
    </div>`;
  }

  function agentPage() {
    return `<div class="page-layout">
      ${pageBar("Mesh 工程师", "agent", { actions: `${button("编辑", "edit", "edit-agent")}${button("发起聊天", "chat", "go-chat", "primary")}` })}
      <div class="page-scroll"><div class="page-content">
        <div class="page-intro">
          <div style="display:flex;gap:14px;align-items:center"><span class="avatar avatar--hero avatar--agent">M</span><div class="page-intro__copy"><h1>Mesh 工程师</h1><p>负责架构、跨模块开发与复杂问题排查。</p></div></div>
          ${statusPill("progress", "正在工作")}
        </div>
        <div class="segmented" style="margin-bottom:16px"><button class="is-active">概览</button><button>活动</button><button>Skills</button><button>配置</button></div>
        <div class="chart-grid">
          <section class="surface-card">
            <div class="surface-card__head"><h2>当前任务</h2><span class="u-spacer"></span><span class="u-muted" style="font-size:11px">运行 12 分钟</span></div>
            <div class="surface-card__body">
              <div style="display:flex;align-items:center;gap:9px;margin-bottom:12px">${statusPill("progress", "进行中")}<span class="u-mono u-muted">MES-147</span></div>
              <h3 style="margin:0 0 7px;font-size:15px">移动端看板横向滚动时保持列标题可见</h3>
              <p class="u-muted" style="margin:0;font-size:12px;line-height:18px">正在运行浏览器走查，并核对三个移动视口下的滚动与拖动手势。</p>
              <div class="progress-track" style="margin-top:18px"><i style="width:72%"></i></div>
            </div>
          </section>
          <section class="surface-card">
            <div class="surface-card__head"><h2>本周表现</h2></div>
            <div class="surface-card__body" style="display:grid;gap:14px">
              <div style="display:flex"><span class="u-muted">完成任务</span><span class="u-spacer"></span><strong class="u-mono">18</strong></div>
              <div style="display:flex"><span class="u-muted">成功率</span><span class="u-spacer"></span><strong class="u-mono">94.7%</strong></div>
              <div style="display:flex"><span class="u-muted">平均时长</span><span class="u-spacer"></span><strong class="u-mono">16m 42s</strong></div>
              <div style="display:flex"><span class="u-muted">Token</span><span class="u-spacer"></span><strong class="u-mono">428k</strong></div>
            </div>
          </section>
        </div>
        <section class="surface-card" style="margin-top:12px">
          <div class="surface-card__head"><h2>已绑定 Skills</h2><span class="u-spacer"></span>${button("管理", "settings", "go-skills")}</div>
          <div class="surface-card__body" style="display:flex;flex-wrap:wrap;gap:8px">
            <span class="meta-pill">${icon("skill", "icon--sm")}界面评审</span><span class="meta-pill">${icon("skill", "icon--sm")}代码审查</span><span class="meta-pill">${icon("skill", "icon--sm")}发布准备</span><span class="meta-pill">${icon("skill", "icon--sm")}问题诊断</span>
          </div>
        </section>
      </div></div>
    </div>`;
  }

  function skillsPage() {
    return `<div class="page-layout">
      ${pageBar("Skills")}
      <div class="workspace-toolbar">
        <label class="search-field">${icon("search", "icon--sm")}<input class="ui-input" placeholder="Search skills..." /></label>
        <span class="u-spacer"></span>
        ${button("New skill", "plus", "import-skill", "outline")}
      </div>
      <div class="workspace-empty workspace-empty--illustrated">
        <span class="empty-glyph">${icon("skill", "icon--lg")}</span>
        <h2>Create a reusable skill</h2>
        <p>Skills give Mesh teammates a shared, repeatable way to work.</p>
        ${button("New skill", "plus", "import-skill", "outline")}
      </div>
    </div>`;
  }

  function skillPage() {
    return `<div class="page-layout">
      ${pageBar("界面评审", "skill", { actions: `${button("编辑", "edit", "edit-skill")}${button("绑定智能体", "plus", "bind-skill", "primary")}` })}
      <div class="page-scroll"><div class="page-content">
        <div class="page-intro"><div style="display:flex;gap:13px;align-items:center"><span class="entity-card__glyph" style="width:48px;height:48px">${icon("skill", "icon--lg")}</span><div class="page-intro__copy"><h1>界面评审</h1><p>以真实浏览器验证布局、交互、主题和响应式质量。</p></div></div><span class="meta-pill">v1.4</span></div>
        <div class="segmented" style="margin-bottom:14px"><button class="is-active">说明</button><button>文件</button><button>已绑定</button><button>历史</button></div>
        <section class="surface-card">
          <div class="surface-card__head"><h2>能力说明</h2><span class="u-spacer"></span><span class="u-muted u-mono" style="font-size:11px">SKILL.md</span></div>
          <div class="surface-card__body prose" style="max-width:none;margin-top:0">
            <p><strong>目标</strong></p>
            <p>用真实用户路径检查界面是否在桌面与移动设备上保持清晰、可达且一致。</p>
            <p><strong>检查顺序</strong></p>
            <ul><li>先验证关键任务能否顺畅完成。</li><li>再检查亮暗主题、空态、加载态和错误态。</li><li>最后对照视觉基线记录可复现差异。</li></ul>
            <p><strong>输出</strong></p>
            <p>提供页面、视口、操作路径、预期与实际结果；视觉问题附带可定位的截图说明。</p>
          </div>
        </section>
      </div></div>
    </div>`;
  }

  function squadsPage() {
    const squads = [
      ["Mesh 产品小队", "产品、设计与工程协同", "you", "3 teammates", "Active now"],
    ];
    return `<div class="page-layout">
      ${pageBar("Squads")}
      <div class="workspace-toolbar">
        <div class="workspace-tabs"><button class="is-active" type="button">Mine</button><button type="button">All</button></div>
        <span class="u-spacer"></span>
        ${button("Filter", "filter", "filter-placeholder")}
        ${button("Updated", "sort", "filter-placeholder")}
        ${button("New squad", "plus", "filter-placeholder", "outline")}
      </div>
      <div class="workspace-table-wrap">
        <table class="workspace-table">
          <thead><tr><th>Name</th><th>Leader</th><th>Members</th><th>Last active</th><th></th></tr></thead>
          <tbody>${squads.map(([name, detail, leader, members, active]) => `<tr>
            <td><span class="squad-tile">${icon("members", "icon--sm")}</span><span><strong>${name}</strong><small>${detail}</small></span></td>
            <td>${avatar(leader)}<span>陈闻峰</span></td><td>${members}</td><td>${active}</td><td>${icon("more", "icon--sm")}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
    </div>`;
  }

  function runtimesPage() {
    return `<div class="page-layout">
      ${pageBar("Runtimes")}
      <div class="workspace-toolbar">
        <span class="toolbar-label">Computers</span>
        <span class="u-spacer"></span>
        ${button("Add a computer", "plus", "filter-placeholder", "outline")}
      </div>
      <div class="runtime-list">
        <article class="runtime-card">
          <span class="runtime-device">${icon("runtime", "icon--lg")}</span>
          <span class="runtime-card__copy"><strong>Mesh development computer</strong><small>Local runtime · v0.1.0</small></span>
          <span class="runtime-state"><i></i>Online</span>
          <span class="runtime-meta"><small>Last seen</small><strong>Just now</strong></span>
          ${button("Manage", "settings", "filter-placeholder", "outline")}
        </article>
      </div>
    </div>`;
  }

  function automationsPage() {
    return `<div class="page-layout">
      ${pageBar("Autopilot")}
      <div class="page-scroll"><div class="page-content">
        <div class="page-intro"><div class="page-intro__copy"><h1>自动化</h1><p>让重复工作按计划、Webhook 或人工触发自动执行。</p></div></div>
        <div class="toolbar surface-card" style="margin-bottom:12px;border-radius:10px"><div class="segmented"><button class="is-active">全部</button><button>已启用</button><button>已暂停</button></div><span class="u-spacer"></span>${button("运行记录", "clock", "automation-runs")}</div>
        <div class="automation-list">
          ${state.automations
            .map(
              (item, index) => `<article class="automation-row">
                <span class="automation-icon">${icon(index % 2 ? "clock" : "zap", "icon--lg")}</span>
                <div class="automation-name"><strong>${item.name}</strong><span>${item.detail}</span></div>
                <div class="automation-row__schedule"><div class="u-muted" style="font-size:11px">触发</div><div style="font-size:12px">${item.schedule}</div></div>
                <div class="automation-row__target"><div class="u-muted" style="font-size:11px">目标</div><div style="font-size:12px">${item.target}</div></div>
                <div class="automation-row__last-run"><div class="u-muted" style="font-size:11px">最近运行</div><div style="font-size:12px">${item.run}</div></div>
                <button class="switch" type="button" role="switch" aria-checked="${item.enabled}" aria-label="${item.enabled ? "暂停" : "启用"}自动化" data-action="toggle-automation" data-index="${index}"></button>
              </article>`,
            )
            .join("")}
        </div>
      </div></div>
    </div>`;
  }

  function inboxPage() {
    const notifications = [
      ["design", "设计助手在 MES-147 中提及了你", "390px 下第四列的 WIP 数量会被折行，建议改为单行网格。", "3 分钟"],
      ["agent", "MES-146 已进入评审", "登录成功后的原始路径恢复已完成，并通过重定向安全用例。", "21 分钟"],
      ["lin", "林澄更新了项目健康状态", "协作体验本周进展正常，评论与收件箱已完成联调。", "1 小时"],
      ["agent", "自动化运行完成", "高优先级 Issue 巡检发现 2 个即将超时的事项。", "2 小时"],
      ["zhao", "赵可回复了你的评论", "附件预览的尺寸已经统一，移动端也同步验证过了。", "昨天"],
    ];
    const selected = notifications[state.inboxSelected];
    return `<div class="page-layout">
      ${pageBar("Inbox")}
      <div class="split-workspace">
        <div class="split-list">
          <div class="inbox-filter"><div class="workspace-tabs"><button class="is-active">Inbox</button><button>Archive</button></div><span class="u-spacer"></span><button class="ui-button ui-button--icon" type="button">${icon("filter", "icon--sm")}</button></div>
          ${notifications
            .map(
              ([person, title, preview, time], index) => `<article class="inbox-item ${index < 4 ? "is-unread" : ""} ${state.inboxSelected === index ? "is-selected" : ""}" data-action="select-inbox" data-index="${index}">
                ${avatar(person)}
                <div class="inbox-item__copy"><div class="inbox-item__line"><span class="inbox-item__title">${title}</span><time class="inbox-item__time">${time}</time></div><div class="inbox-item__preview">${preview}</div></div>
              </article>`,
            )
            .join("")}
        </div>
        <article class="split-detail">
          ${
            selected
              ? `<div class="notification-detail">
                  <div class="notification-detail__eyebrow">MES-147 · COMMENT</div>
                  <h1>${selected[1]}</h1>
                  <div class="notification-detail__meta">${avatar(selected[0])}<span>${people[selected[0]].name}</span><span>·</span><span>${selected[3]}前</span></div>
                  <div class="notification-quote">
                    <p>${selected[2]}</p>
                    <pre><code>.board-column__head {
  position: sticky;
  top: 0;
}</code></pre>
                  </div>
                  <form class="composer composer--compact" data-form="comment">
                    <textarea class="ui-textarea" name="comment" placeholder="Leave a comment..."></textarea>
                    <div class="composer__bar"><button class="ui-button ui-button--icon" type="button">${icon("attach")}</button><span class="u-spacer"></span><button class="ui-button ui-button--small ui-button--primary" type="submit">${icon("send", "icon--sm")}Send</button></div>
                  </form>
                </div>`
              : `<div class="split-placeholder"><span>${icon("inbox", "icon--lg")}</span><p>Select an inbox item to view details</p></div>`
          }
        </article>
      </div>
    </div>`;
  }

  function chatPage() {
    const sessions = [
      ["移动端看板走查", "Mesh 工程师", "刚刚"],
      ["本周交付风险", "数据分析师", "1 小时"],
      ["登录安全复核", "Mesh 工程师", "昨天"],
      ["项目更新文案", "文档维护员", "周二"],
      ["主题对比度", "设计助手", "周一"],
    ];
    return `<div class="page-layout">
      ${pageBar("Chat")}
      <div class="chat-shell">
        <aside class="chat-sessions">
          <div class="chat-sessions__head"><strong>Conversations</strong><span class="u-spacer"></span>${button("New", "plus", "new-chat", "outline")}</div>
          ${sessions.map(([title, agent, time], index) => `<button class="session-item ${state.chatSelected === index ? "is-active" : ""}" type="button" data-action="select-chat" data-index="${index}"><div class="session-item__title">${title}</div><div class="session-item__meta"><span>${agent}</span><span>${time}</span></div></button>`).join("")}
        </aside>
        ${
          state.chatSelected < 0
            ? `<section class="chat-room chat-room--empty"><div class="split-placeholder"><span>${icon("chat", "icon--lg")}</span><p>Select a conversation to start chatting</p></div></section>`
            : `<section class="chat-room">
                <header class="chat-room__head">${avatar("agent")}<div><strong>Mesh 工程师</strong><span class="u-success">Online</span></div><span class="u-spacer"></span><span class="meta-pill">${icon("link", "icon--sm")}MES-147</span><button class="ui-button ui-button--icon" type="button">${icon("more")}</button></header>
                <div class="chat-messages"><div class="chat-thread" data-chat-thread>
                  ${state.messages
                    .map(
                      (message) =>
                        message.from === "me"
                          ? `<div class="message message--me"><div class="message__body"><p>${escapeHtml(message.text)}</p><div class="message__meta">${message.time}</div></div>${avatar("you")}</div>`
                          : `<div class="message">${avatar("agent")}<div class="message__body"><p>${escapeHtml(message.text)}</p><div class="message__meta">Mesh 工程师 · ${message.time}</div></div></div>`,
                    )
                    .join("")}
                </div></div>
                <div class="chat-compose-wrap">
                  <form class="chat-compose" data-form="chat">
                    <textarea name="message" placeholder="Message Mesh 工程师…" aria-label="Message"></textarea>
                    <div class="chat-compose__foot"><button class="ui-button ui-button--icon" type="button">${icon("attach")}</button><span class="u-spacer"></span><button class="ui-button ui-button--icon ui-button--primary" type="submit" aria-label="Send">${icon("send")}</button></div>
                  </form>
                </div>
              </section>`
        }
      </div>
    </div>`;
  }

  function analyticsPage() {
    const bars = [42, 54, 38, 64, 71, 60, 84, 74, 91, 68, 77, 88, 64, 96];
    const metricPeople = [
      ["agent", "Mesh 工程师", 86, "428k"],
      ["design", "设计助手", 68, "312k"],
      ["qiao", "文档维护员", 48, "221k"],
      ["lin", "数据分析师", 34, "156k"],
    ];
    return `<div class="page-layout">
      ${pageBar("分析", "chart", { actions: `${button("最近 30 天", "calendar", "filter-placeholder")}${button("导出", "arrow", "export-data")}` })}
      <div class="page-scroll"><div class="page-content page-content--wide">
        <div class="page-intro"><div class="page-intro__copy"><h1>工作区分析</h1><p>了解交付速度、智能体用量与运行质量。</p></div></div>
        <div class="kpi-grid">
          <div class="kpi-card"><div class="kpi-card__label">完成任务 ${icon("check", "icon--sm")}</div><div class="kpi-card__value u-mono">184</div><div class="kpi-card__trend">↑ 12.6% 较上期</div></div>
          <div class="kpi-card"><div class="kpi-card__label">运行时长 ${icon("clock", "icon--sm")}</div><div class="kpi-card__value u-mono">72.4h</div><div class="kpi-card__trend">↑ 8.1% 较上期</div></div>
          <div class="kpi-card"><div class="kpi-card__label">Token ${icon("zap", "icon--sm")}</div><div class="kpi-card__value u-mono">1.24m</div><div class="kpi-card__trend">↓ 4.3% 单任务</div></div>
          <div class="kpi-card"><div class="kpi-card__label">成功率 ${icon("shield", "icon--sm")}</div><div class="kpi-card__value u-mono">94.7%</div><div class="kpi-card__trend">↑ 2.2 个百分点</div></div>
        </div>
        <div class="chart-grid">
          <section class="chart-panel">
            <div class="chart-panel__head"><h3>每日完成任务</h3><div class="segmented"><button class="is-active">任务</button><button>时长</button><button>Token</button></div></div>
            <div class="bar-chart">${bars.map((height, index) => `<i class="bar-chart__bar" style="height:${height}%" data-label="${index + 17}"></i>`).join("")}</div>
          </section>
          <section class="chart-panel">
            <div class="chart-panel__head"><h3>智能体用量</h3><span class="u-muted" style="font-size:11px">Token</span></div>
            <div class="agent-metric-list">
              ${metricPeople.map(([person, name, width, value]) => `<div class="agent-metric">${avatar(person)}<div class="agent-metric__copy"><div class="agent-metric__line"><span>${name}</span><span class="u-mono u-muted">${value}</span></div><div class="progress-track"><i style="width:${width}%"></i></div></div><span class="u-mono" style="font-size:11px">${width}%</span></div>`).join("")}
            </div>
          </section>
        </div>
      </div></div>
    </div>`;
  }

  function settingsPage() {
    const accountItems = [
      ["profile", "Profile", "my"],
      ["preferences", "Preferences", "palette"],
      ["shortcuts", "Shortcuts", "command"],
      ["chat", "Chat", "chat"],
      ["notifications", "Notifications", "bell"],
      ["tokens", "API Tokens", "link"],
    ];
    const workspaceItems = [
      ["general", "General", "settings"],
      ["repositories", "Repositories", "project"],
      ["github", "GitHub", "link"],
      ["integrations", "Integrations", "zap"],
      ["labs", "Labs", "skill"],
      ["members", "Members", "members"],
      ["labels", "Labels", "state"],
    ];
    const currentTheme = document.documentElement.dataset.theme;
    const selectedLabel = [...accountItems, ...workspaceItems].find(([key]) => key === state.settingsTab)?.[1] || "Profile";
    let content = "";
    if (state.settingsTab === "preferences") {
      content = `<section class="settings-section"><h1>Preferences</h1><h2>General</h2>
        <div class="preference-card">
          <label><span>Theme</span><select class="ui-select" aria-label="Theme" data-theme-select>
            <option value="system">System</option><option value="light" ${currentTheme === "light" ? "selected" : ""}>Light</option><option value="dark" ${currentTheme === "dark" ? "selected" : ""}>Dark</option>
          </select></label>
          <label><span>Language</span><select class="ui-select" aria-label="Language"><option>English</option><option>简体中文</option></select></label>
          <label><span><strong>Viewing Timezone</strong><small>Used for dashboards, charts, and any “today” label shown to you.</small></span><select class="ui-select"><option>Asia/Shanghai (browser)</option></select></label>
          <label><span><strong>Sticky comment bar</strong><small>Keep the comment bar pinned while scrolling an issue page.</small></span><button class="switch" type="button" role="switch" aria-checked="true" data-action="toggle-switch"></button></label>
        </div>
      </section>`;
    } else if (state.settingsTab === "profile") {
      content = `<section class="settings-section"><h1>Profile</h1><h2>Personal information</h2>
        <div class="profile-form">
          <label class="avatar-field"><span>Avatar<small>Click to upload avatar</small></span>${avatar("you", true)}</label>
          <label><span>Name</span><input class="ui-input" value="陈闻峰" /></label>
          <label><span>About you<small>Shared with Mesh teammates working on your behalf.</small></span><textarea class="ui-textarea" rows="5">产品与工程负责人，关注 AI 原生协作体验与交付质量。</textarea></label>
        </div>
      </section>`;
    } else {
      content = `<section class="settings-section"><h1>${selectedLabel}</h1><h2>${accountItems.some(([key]) => key === state.settingsTab) ? "My Account" : "Mesh"}</h2>
        <div class="preference-card"><label><span>${selectedLabel}</span><span class="u-muted">Configuration for this static prototype.</span></label></div>
      </section>`;
    }
    return `<div class="page-layout">
      ${pageBar("Settings")}
      <div class="settings-layout">
        <nav class="settings-nav">
          <div class="settings-nav__title">My Account</div>
          ${accountItems.map(([key, label, iconName]) => `<button class="${state.settingsTab === key ? "is-active" : ""}" type="button" data-action="settings-tab" data-tab="${key}">${icon(iconName, "icon--sm")}${label}</button>`).join("")}
          <div class="settings-nav__title">Mesh</div>
          ${workspaceItems.map(([key, label, iconName]) => `<button class="${state.settingsTab === key ? "is-active" : ""}" type="button" data-action="settings-tab" data-tab="${key}">${icon(iconName, "icon--sm")}${label}</button>`).join("")}
        </nav>
        <div class="settings-content">${content}</div>
      </div>
    </div>`;
  }

  function statesPage() {
    return `<div class="page-layout">
      ${pageBar("状态画廊", "state")}
      <div class="page-scroll"><div class="page-content">
        <div class="page-intro"><div class="page-intro__copy"><h1>页面状态</h1><p>关键页面共用的空态、加载态和错误态视觉基线。</p></div></div>
        <div class="state-showcase">
          <section class="state-card"><div class="state-card__inner"><span class="state-icon">${icon("inbox", "icon--xl")}</span><h3>这里还没有内容</h3><p>创建第一项内容后，它会出现在这里并保持同步。</p>${button("创建第一项", "plus", "open-create-issue", "primary")}</div></section>
          <section class="state-card"><div class="state-card__inner"><span class="typing-dots"><i></i><i></i><i></i></span><h3>正在加载工作区</h3><p>正在获取最新数据和团队活动，请稍候。</p><div class="skeleton-lines"><i class="skeleton-line"></i><i class="skeleton-line"></i><i class="skeleton-line"></i></div></div></section>
          <section class="state-card"><div class="state-card__inner"><span class="state-icon u-danger">${icon("alert", "icon--xl")}</span><h3>暂时无法加载</h3><p>连接服务时出现问题。你的本地更改已经保留。</p>${button("重新尝试", "refresh", "retry-state", "outline")}</div></section>
        </div>
      </div></div>
    </div>`;
  }

  const renderers = {
    inbox: inboxPage,
    chat: chatPage,
    my: myIssuesPage,
    issues: issuesPage,
    board: boardPage,
    issue: issuePage,
    projects: projectsPage,
    project: projectPage,
    members: membersPage,
    agents: agentsPage,
    agent: agentPage,
    squads: squadsPage,
    runtimes: runtimesPage,
    skills: skillsPage,
    skill: skillPage,
    autopilot: automationsPage,
    automations: automationsPage,
    usage: analyticsPage,
    analytics: analyticsPage,
    settings: settingsPage,
    states: statesPage,
  };

  function render() {
    state.route = currentRoute();
    overlayRoot.innerHTML = "";
    if (["login", "register", "code"].includes(state.route)) {
      root.innerHTML = authPage(state.route);
      document.title = `${state.route === "register" ? "注册" : "登录"} · Mesh`;
      return;
    }
    const renderer = renderers[state.route] || issuesPage;
    root.innerHTML = appShell(renderer());
    document.title = `${(routeInfo[state.route] || routeInfo.issues).label} · Mesh`;
    bindDragAndDrop();
    requestAnimationFrame(() => {
      const chat = root.querySelector(".chat-messages");
      if (chat) chat.scrollTop = chat.scrollHeight;
    });
  }

  function showToast(title, detail = "") {
    const node = document.createElement("div");
    node.className = "toast";
    node.innerHTML = `${icon("check")}<div><strong>${escapeHtml(title)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</div>`;
    toastRegion.append(node);
    window.setTimeout(() => node.remove(), 3200);
  }

  function closeOverlay() {
    overlayRoot.innerHTML = "";
  }

  function openDialog(content, className = "") {
    overlayRoot.innerHTML = `<div class="overlay-backdrop" data-action="close-overlay"><section class="dialog ${className}" role="dialog" aria-modal="true" data-dialog>${content}</section></div>`;
    const autofocus = overlayRoot.querySelector("[autofocus]");
    if (autofocus) window.setTimeout(() => autofocus.focus(), 0);
  }

  function createIssueDialog() {
    openDialog(`
      <div class="dialog__head"><h2>新建 Issue</h2><span class="u-spacer"></span><button class="ui-button ui-button--icon" type="button" data-action="close-overlay">${icon("close")}</button></div>
      <form data-form="create-issue">
        <div class="dialog__body modal-form">
          <div class="form-field"><label class="form-label" for="issue-title">标题</label><input id="issue-title" class="ui-input" name="title" placeholder="需要完成什么？" required autofocus /></div>
          <div class="form-field"><label class="form-label" for="issue-description">描述</label><textarea id="issue-description" class="ui-textarea" name="description" placeholder="补充背景、范围与完成标准…"></textarea></div>
          <div class="modal-form__row">
            <div class="form-field"><label class="form-label">状态</label><select class="ui-select" name="status"><option value="todo">待办</option><option value="progress">进行中</option></select></div>
            <div class="form-field"><label class="form-label">负责人</label><select class="ui-select" name="assignee"><option value="agent">Mesh 工程师</option><option value="you">陈闻峰</option><option value="lin">林澄</option></select></div>
          </div>
          <div class="modal-form__row">
            <div class="form-field"><label class="form-label">优先级</label><select class="ui-select" name="priority"><option value="normal">普通</option><option value="high">高</option><option value="urgent">紧急</option></select></div>
            <div class="form-field"><label class="form-label">项目</label><select class="ui-select" name="project"><option>移动体验</option><option>身份与权限</option><option>协作体验</option></select></div>
          </div>
        </div>
        <div class="dialog__foot"><button class="ui-button ui-button--small ui-button--outline" type="button" data-action="close-overlay">取消</button><button class="ui-button ui-button--small ui-button--primary" type="submit">${icon("plus", "icon--sm")}创建 Issue</button></div>
      </form>
    `);
  }

  function workspaceDialog() {
    const workspaces = [
      ["MS", "Mesh Studio", "产品与工程"],
      ["PL", "产品实验室", "6 位成员"],
      ["IF", "Infra", "基础设施"],
    ];
    openDialog(`
      <div class="dialog__head"><h2>切换工作区</h2><span class="u-spacer"></span><button class="ui-button ui-button--icon" type="button" data-action="close-overlay">${icon("close")}</button></div>
      <div class="dialog__body">
        <div class="workspace-menu">
          ${workspaces.map(([code, name, detail]) => `<button class="workspace-option ${state.workspace === name ? "is-active" : ""}" type="button" data-action="select-workspace" data-code="${code}" data-name="${name}"><span class="workspace-icon">${code}</span><span class="workspace-trigger__copy"><strong>${name}</strong><span class="u-muted" style="font-size:11px">${detail}</span></span>${state.workspace === name ? icon("check") : ""}</button>`).join("")}
        </div>
      </div>
      <div class="dialog__foot" style="justify-content:space-between"><button class="ui-button ui-button--small" type="button" data-action="workspace-settings">${icon("settings", "icon--sm")}工作区设置</button><button class="ui-button ui-button--small ui-button--primary" type="button" data-action="create-workspace">${icon("plus", "icon--sm")}创建工作区</button></div>
    `);
  }

  function createWorkspaceDialog() {
    openDialog(`
      <div class="dialog__head"><button class="ui-button ui-button--icon" type="button" data-action="open-workspaces">${icon("chevron", "icon--sm")}</button><h2>创建工作区</h2><span class="u-spacer"></span><button class="ui-button ui-button--icon" type="button" data-action="close-overlay">${icon("close")}</button></div>
      <form data-form="create-workspace">
        <div class="dialog__body modal-form">
          <div class="form-field"><label class="form-label" for="workspace-name">工作区名称</label><input id="workspace-name" class="ui-input" name="name" placeholder="例如：产品团队" required autofocus /><span class="form-hint">稍后可以在工作区设置中修改。</span></div>
          <div class="form-field"><label class="form-label" for="workspace-code">Issue 标识</label><input id="workspace-code" class="ui-input u-mono" name="code" value="MES" maxlength="4" required /></div>
        </div>
        <div class="dialog__foot"><button class="ui-button ui-button--small ui-button--outline" type="button" data-action="open-workspaces">返回</button><button class="ui-button ui-button--small ui-button--primary" type="submit">创建工作区</button></div>
      </form>
    `);
  }

  function commandDialog() {
    const query = state.commandQuery.toLowerCase();
    const commands = [
      ["issues", "打开 Issues", "issues", "G I"],
      ["projects", "打开项目", "project", "G P"],
      ["agents", "管理智能体", "agent", ""],
      ["squads", "打开 Squads", "members", ""],
      ["runtimes", "打开 Runtimes", "runtime", ""],
      ["usage", "查看 Usage", "chart", ""],
      ["settings", "打开设置", "settings", ""],
      ["action:create", "新建 Issue", "plus", "C"],
      ["action:theme", "打开主题偏好", "palette", ""],
    ].filter((command) => command[1].toLowerCase().includes(query));
    openDialog(`
      <div class="command-field">${icon("search", "command-search-icon")}<input class="command-input" data-command-input placeholder="搜索页面、Issue 或运行命令…" value="${escapeHtml(state.commandQuery)}" autofocus /></div>
      <div class="command-list">
        <div class="command-group-title">${query ? "搜索结果" : "建议"}</div>
        ${commands.length ? commands.map(([route, label, iconName, key]) => `<button class="command-item" type="button" data-command-route="${route}">${icon(iconName)}<span>${label}</span>${key ? `<kbd class="shortcut">${key}</kbd>` : ""}</button>`).join("") : `<div class="no-results" style="min-height:130px">没有匹配的命令</div>`}
      </div>
      <div class="dialog__foot" style="justify-content:flex-start;color:var(--ink-soft);font-size:11px"><kbd class="shortcut">↑↓</kbd><span>选择</span><kbd class="shortcut">↵</kbd><span>打开</span><kbd class="shortcut">esc</kbd><span>关闭</span></div>
    `, "dialog--command");
  }

  function profileDialog() {
    openDialog(`
      <div class="dialog__body">
        <div class="roster-person" style="padding:4px 3px 12px">${avatar("you", true)}<div class="roster-person__copy"><div class="roster-person__name">陈闻峰</div><div class="roster-person__email">cnwenf@outlook.com</div></div></div>
        <div class="workspace-menu">
          <button class="command-item" type="button" data-action="go-profile">${icon("my")}个人资料</button>
          <button class="command-item u-danger" type="button" data-route="login">${icon("logout")}退出登录</button>
        </div>
      </div>
    `);
  }

  function mobileDrawer() {
    overlayRoot.innerHTML = `<div class="drawer-backdrop" data-action="close-overlay"></div><div class="mobile-drawer">${sidebar()}</div>`;
  }

  function bindDragAndDrop() {
    let dragged = null;
    root.querySelectorAll(".board-card").forEach((card) => {
      card.addEventListener("dragstart", (event) => {
        dragged = { key: card.dataset.cardKey, column: card.dataset.column };
        card.classList.add("is-dragging");
        event.dataTransfer.effectAllowed = "move";
      });
      card.addEventListener("dragend", () => {
        card.classList.remove("is-dragging");
        root.querySelectorAll(".board-column").forEach((column) => column.classList.remove("is-dragover"));
      });
    });
    root.querySelectorAll("[data-drop-column]").forEach((column) => {
      column.addEventListener("dragover", (event) => {
        event.preventDefault();
        column.classList.add("is-dragover");
      });
      column.addEventListener("dragleave", () => column.classList.remove("is-dragover"));
      column.addEventListener("drop", (event) => {
        event.preventDefault();
        if (!dragged) return;
        const destination = column.dataset.dropColumn;
        const source = state.board[dragged.column];
        const index = source.findIndex((card) => card.key === dragged.key);
        if (index === -1 || destination === dragged.column) return;
        const [moved] = source.splice(index, 1);
        state.board[destination].push(moved);
        render();
        showToast(`${moved.key} 已移动`, `状态更新为${{ todo: "待办", progress: "进行中", review: "待评审", done: "已完成" }[destination]}`);
      });
    });
  }

  root.addEventListener("click", (event) => {
    const routeTarget = event.target.closest("[data-route]");
    if (routeTarget) {
      event.preventDefault();
      setRoute(routeTarget.dataset.route);
      closeOverlay();
      return;
    }
    const actionTarget = event.target.closest("[data-action]");
    if (!actionTarget) return;
    const action = actionTarget.dataset.action;
    const actions = {
      "toggle-theme": () => {
        const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = theme;
        localStorage.setItem("mesh-prototype-theme", theme);
        render();
        closeOverlay();
        showToast(theme === "dark" ? "已切换到暗色主题" : "已切换到亮色主题");
      },
      "open-create-issue": createIssueDialog,
      "open-workspaces": workspaceDialog,
      "create-workspace": createWorkspaceDialog,
      "close-overlay": closeOverlay,
      "download-desktop": () => showToast("Mesh Desktop", "Desktop download will be available from the release page."),
      "open-command": () => {
        state.commandQuery = "";
        commandDialog();
      },
      "mobile-menu": mobileDrawer,
      "profile-menu": profileDialog,
      "workspace-settings": () => {
        state.settingsTab = "general";
        setRoute("settings");
        closeOverlay();
      },
      "create-project": () => showToast("项目创建面板已就绪", "静态原型中使用示例项目展示布局。"),
      "invite-member": () => showToast("邀请已复制", "邀请链接有效期为 7 天。"),
      "create-agent": () => showToast("智能体创建向导已打开", "选择职责、运行时与 Skills 后即可加入团队。"),
      "import-skill": () => showToast("Skill 导入向导已打开", "支持 URL、文件或工作区模板。"),
      "create-automation": () => showToast("自动化创建向导已打开", "请选择定时、Webhook 或手动触发。"),
      "mark-read": () => showToast("收件箱已清空未读", "5 条通知已标为已读。"),
      "open-selected-issue": () => setRoute("issue"),
      "archive-notification": () => showToast("通知已归档"),
      "select-inbox": () => {
        state.inboxSelected = Number(actionTarget.dataset.index || 0);
        render();
      },
      "select-chat": () => {
        state.chatSelected = Number(actionTarget.dataset.index || 0);
        render();
      },
      "settings-tab": () => {
        state.settingsTab = actionTarget.dataset.tab;
        render();
      },
      "set-theme": () => {
        const requested = actionTarget.dataset.theme;
        const theme = requested === "system"
          ? matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
          : requested;
        document.documentElement.dataset.theme = theme;
        if (requested === "system") localStorage.removeItem("mesh-prototype-theme");
        else localStorage.setItem("mesh-prototype-theme", theme);
        render();
        showToast(requested === "system" ? "主题将跟随系统" : `已切换到${theme === "dark" ? "暗色" : "亮色"}主题`);
      },
      "toggle-switch": () => {
        const next = actionTarget.getAttribute("aria-checked") !== "true";
        actionTarget.setAttribute("aria-checked", String(next));
      },
      "toggle-automation": () => {
        const index = Number(actionTarget.dataset.index);
        state.automations[index].enabled = !state.automations[index].enabled;
        render();
        showToast(state.automations[index].enabled ? "自动化已启用" : "自动化已暂停", state.automations[index].name);
      },
      "select-workspace": () => {
        state.workspace = actionTarget.dataset.name;
        state.workspaceCode = actionTarget.dataset.code;
        closeOverlay();
        render();
        showToast(`已切换到 ${state.workspace}`);
      },
      "go-chat": () => setRoute("chat"),
      "go-skills": () => setRoute("skills"),
      "go-profile": () => {
        state.settingsTab = "profile";
        setRoute("settings");
      },
      "save-settings": () => showToast("设置已保存"),
      "retry-state": () => {
        actionTarget.disabled = true;
        actionTarget.innerHTML = `<span class="typing-dots"><i></i><i></i><i></i></span>重试中`;
        window.setTimeout(() => showToast("连接已恢复", "工作区数据已重新同步。"), 700);
      },
      "follow-issue": () => showToast("已关注 MES-147", "后续更新会进入你的收件箱。"),
      "sort-issues": () => {
        state.issues.reverse();
        render();
        showToast("已反转更新时间排序");
      },
      "filter-placeholder": () => showToast("筛选菜单", "静态原型保留了完整的触发与反馈状态。"),
      "automation-runs": () => showToast("运行记录", "最近 30 天共运行 182 次，成功率 96.2%。"),
      "export-data": () => showToast("导出任务已创建", "CSV 文件准备完成后会出现在收件箱。"),
      "logout-others": () => showToast("其他设备已退出", "当前会话保持登录。"),
      "new-chat": () => showToast("已创建新对话", "选择一个智能体开始协作。"),
      "project-settings": () => {
        state.settingsTab = "general";
        setRoute("settings");
      },
      "edit-agent": () => showToast("智能体编辑面板已打开"),
      "edit-skill": () => showToast("Skill 编辑器已打开"),
      "bind-skill": () => showToast("已打开智能体选择器"),
    };
    (actions[action] || (() => {}))();
  });

  overlayRoot.addEventListener("click", (event) => {
    const command = event.target.closest("[data-command-route]");
    if (command) {
      const route = command.dataset.commandRoute;
      closeOverlay();
      if (route === "action:create") createIssueDialog();
      else if (route === "action:theme") {
        state.settingsTab = "preferences";
        setRoute("settings");
      } else setRoute(route);
      return;
    }
    const routeTarget = event.target.closest("[data-route]");
    if (routeTarget) {
      setRoute(routeTarget.dataset.route);
      closeOverlay();
      return;
    }
    const actionTarget = event.target.closest("[data-action]");
    if (!actionTarget) return;
    if (actionTarget.dataset.action === "close-overlay" && !event.target.closest("[data-dialog]")) closeOverlay();
    else if (actionTarget.dataset.action === "close-overlay") closeOverlay();
    else if (actionTarget.dataset.action === "open-workspaces") workspaceDialog();
    else if (actionTarget.dataset.action === "create-workspace") createWorkspaceDialog();
    else if (actionTarget.dataset.action === "workspace-settings") {
      state.settingsTab = "general";
      closeOverlay();
      setRoute("settings");
    } else if (actionTarget.dataset.action === "select-workspace") {
      state.workspace = actionTarget.dataset.name;
      state.workspaceCode = actionTarget.dataset.code;
      closeOverlay();
      render();
      showToast(`已切换到 ${state.workspace}`);
    } else if (actionTarget.dataset.action === "toggle-theme") {
      const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      localStorage.setItem("mesh-prototype-theme", theme);
      closeOverlay();
      render();
      showToast("主题已切换");
    } else if (actionTarget.dataset.action === "go-profile") {
      state.settingsTab = "profile";
      closeOverlay();
      setRoute("settings");
    }
  });

  document.addEventListener("input", (event) => {
    const authForm = event.target.closest('form[data-form="login"], form[data-form="register"], form[data-form="verify-code"]');
    if (authForm) {
      const submit = authForm.querySelector("[data-auth-submit]");
      if (submit) submit.disabled = !authForm.checkValidity();
      if (event.target.matches(".otp-cell") && event.target.value) {
        const next = event.target.nextElementSibling;
        if (next?.matches(".otp-cell")) next.focus();
      }
    }
    if (event.target.matches("[data-command-input]")) {
      state.commandQuery = event.target.value;
      commandDialog();
      const input = overlayRoot.querySelector("[data-command-input]");
      input.setSelectionRange(input.value.length, input.value.length);
    }
    if (event.target.matches("[data-filter-issues]")) {
      const query = event.target.value.toLowerCase();
      root.querySelectorAll(".data-table tbody tr").forEach((row) => {
        row.hidden = !row.textContent.toLowerCase().includes(query);
      });
    }
  });

  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-theme-select]")) return;
    const requested = event.target.value;
    const theme = requested === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
      : requested;
    document.documentElement.dataset.theme = theme;
    if (requested === "system") localStorage.removeItem("mesh-prototype-theme");
    else localStorage.setItem("mesh-prototype-theme", theme);
    render();
    showToast("Changes saved");
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-form]");
    if (!form) return;
    event.preventDefault();
    const data = new FormData(form);
    switch (form.dataset.form) {
      case "login":
      case "register": {
        state.authEmail = String(data.get("email") || "");
        setRoute("code");
        break;
      }
      case "verify-code":
        setRoute("issues");
        showToast("欢迎回到 Mesh", "工作区数据已同步。");
        break;
      case "create-issue": {
        const title = String(data.get("title") || "").trim();
        if (!title) return;
        state.issueSequence += 1;
        const key = `MES-${state.issueSequence}`;
        const status = String(data.get("status") || "todo");
        const issue = {
          key,
          title,
          status,
          statusText: status === "progress" ? "进行中" : "待办",
          priority: String(data.get("priority") || "normal"),
          assignee: String(data.get("assignee") || "agent"),
          project: String(data.get("project") || "移动体验"),
          updated: "刚刚",
          label: "新建",
          labelTone: "",
        };
        state.issues.unshift(issue);
        state.board[status].unshift({
          key,
          title,
          label: "新建",
          person: issue.assignee,
          priority: issue.priority,
        });
        closeOverlay();
        render();
        showToast(`${key} 已创建`, title);
        break;
      }
      case "create-workspace": {
        const name = String(data.get("name") || "").trim();
        const code = String(data.get("code") || "MS").trim().slice(0, 3).toUpperCase();
        if (!name) return;
        state.workspace = name;
        state.workspaceCode = code;
        closeOverlay();
        render();
        showToast(`工作区 ${name} 已创建`);
        break;
      }
      case "comment": {
        const text = String(data.get("comment") || "").trim();
        if (!text) return;
        form.reset();
        showToast("评论已发布", "相关订阅者会收到通知。");
        break;
      }
      case "chat": {
        const text = String(data.get("message") || "").trim();
        if (!text) return;
        state.messages.push({ from: "me", text, time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) });
        render();
        window.setTimeout(() => {
          if (state.route !== "chat") return;
          state.messages.push({ from: "agent", text: "收到，我已把这条要求加入当前任务上下文。完成检查后会直接回报结论和需要关注的差异。", time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) });
          render();
        }, 650);
        break;
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    const inField = event.target.matches("input, textarea, select, [contenteditable='true']");
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      state.commandQuery = "";
      commandDialog();
    } else if (event.key === "Escape") {
      closeOverlay();
    } else if (!inField && event.key.toLowerCase() === "c") {
      event.preventDefault();
      createIssueDialog();
    }
  });

  window.addEventListener("hashchange", render);
  window.addEventListener("storage", (event) => {
    if (event.key === "mesh-prototype-theme" && event.newValue) {
      document.documentElement.dataset.theme = event.newValue;
      render();
    }
  });

  render();
})();
