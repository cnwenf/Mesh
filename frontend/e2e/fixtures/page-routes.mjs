/**
 * 视觉回归核心页恒定 fixture 路由(theme.md §5.4 / design-quality §13.5)。
 *
 * 由 mock-server-visual.mjs 引入,仅处理页面专有数据路由;外壳引导路由与字体分发
 * 仍在 mock-server-visual.mjs。所有内容为受控常量(时间戳取自固定基准),供
 * toHaveScreenshot 基线比对。 envelope 语义与 src/api/client.ts 对齐:
 * 单对象 `{data}` / 列表 `{data,next_cursor}` / 分组 `{groups,next_cursor}`。
 */

const BASE_TIME = Date.UTC(2026, 6, 25, 8, 0, 0);
function isoAt(offsetMs) {
  return new Date(BASE_TIME + offsetMs).toISOString();
}

const WORKSPACE_ID = 'ws-1';

/** issue 主键 UUID 形态别名(issue-1 同一 issue 的规范主键寻址,§5.1)。
 * by-identifier 解析返回本形态,使 `/w/{ws}/issues/{uuid}` 直达详情渲染——
 * 若返回 `issue-1` 这类 identifier 形态 id,规范路由会再次触发 by-identifier
 * 重定向构成自循环(MES-79 路由态:IssueByIdRedirect 对 identifier 形态 id 跳解析)。 */
const ISSUE_UUID = '0d3a1f7c-9b2e-4c5a-8f1d-6e7b8c9a0d1e';

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers':
      'Authorization, Content-Type, If-Match, Idempotency-Key, If-None-Match',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    'Access-Control-Expose-Headers': 'ETag, Retry-After',
  };
}

function sendJson(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', ...corsHeaders() });
  res.end(JSON.stringify(body));
}

const single = (data) => ({ data });
const list = (data) => ({ data, next_cursor: null });

// ---------------------------------------------------------------------------
// 看板(BoardPage)
// ---------------------------------------------------------------------------

const VIEW = {
  id: 'view-1',
  workspace_id: WORKSPACE_ID,
  project_id: null,
  owner_member_id: 'member-human-1',
  name: '项目看板',
  layout: 'board',
  visibility: 'shared',
  filters: {},
  group_by: 'state_category',
  sub_group_by: null,
  sort: [],
  display_fields: [],
  board_settings: { columns: ['todo', 'in_progress', 'in_review', 'done'], wip: {} },
  position: 0,
  is_default: true,
  created_at: isoAt(0),
  updated_at: isoAt(0),
  can_write: true,
};

function card(id, identifier, title, category, priority, assigneeName) {
  return {
    id,
    identifier,
    title,
    state_category: category,
    status: { id: `st-${category}`, name: title, category },
    status_id: `st-${category}`,
    priority,
    assignee: assigneeName === null ? null : { id: 'member-human-1', name: assigneeName },
    assignee_id: assigneeName === null ? null : 'member-human-1',
    project_id: null,
    position: 0,
    version: 1,
    updated_at: isoAt(0),
  };
}

const BOARD_GROUPS = {
  layout: 'board',
  group_by: 'state_category',
  column_target_status: {},
  groups: [
    {
      key: 'todo',
      label: 'todo',
      count: 2,
      wip: null,
      data: [
        card('issue-1', 'MESH-1', '设计系统令牌梳理', 'todo', 'high', 'Ana'),
        card('issue-5', 'MESH-5', 'i18n 文案校对', 'todo', 'low', null),
      ],
    },
    {
      key: 'in_progress',
      label: 'in_progress',
      count: 1,
      wip: { limit: 3, enforcement: 'warn' },
      data: [card('issue-2', 'MESH-2', '暗色对比度自证', 'in_progress', 'urgent', 'Ana')],
    },
    {
      key: 'in_review',
      label: 'in_review',
      count: 1,
      wip: null,
      data: [card('issue-3', 'MESH-3', '视觉回归门禁', 'in_review', 'medium', 'Ana')],
    },
    {
      key: 'done',
      label: 'done',
      count: 1,
      wip: null,
      data: [card('issue-4', 'MESH-4', '主题协商链落地', 'done', 'medium', 'Ana')],
    },
  ],
  next_cursor: null,
};

// ---------------------------------------------------------------------------
// issue 详情(IssueDetailPage,issue-1)
// ---------------------------------------------------------------------------

const ISSUE_DETAIL = {
  id: 'issue-1',
  workspace_id: WORKSPACE_ID,
  project_id: null,
  project: null,
  identifier_namespace_key: 'MESH',
  number: 1,
  identifier: 'MESH-1',
  title: '设计系统令牌梳理',
  description: '统一亮色与暗色主题的语义令牌,确保一一对应。',
  status: {
    id: 'st-todo',
    project_id: null,
    name: '待办',
    category: 'todo',
    color: '#6b7280',
    position: 0,
    is_default: true,
    allowed_transitions: [],
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
  status_id: 'st-todo',
  state_category: 'todo',
  priority: 'high',
  assignee: { id: 'member-human-1', name: 'Ana', member_type: 'human' },
  assignee_id: 'member-human-1',
  reporter: { id: 'member-human-1', name: 'Ana', member_type: 'human' },
  reporter_id: 'member-human-1',
  estimate: 3,
  estimate_unit: 'points',
  due_date: '2026-08-01',
  start_date: '2026-07-25',
  milestone_id: null,
  cycle_id: null,
  parent_id: null,
  position: 0,
  completed_at: null,
  version: 4,
  created_at: isoAt(0),
  updated_at: isoAt(3_600_000),
  children_progress: { total: 2, done: 1 },
};

const ISSUE_STATUSES = [
  {
    id: 'st-todo',
    project_id: null,
    name: '待办',
    category: 'todo',
    color: '#6b7280',
    position: 0,
    is_default: true,
    allowed_transitions: [],
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
  {
    id: 'st-in_progress',
    project_id: null,
    name: '进行中',
    category: 'in_progress',
    color: '#2563eb',
    position: 1,
    is_default: false,
    allowed_transitions: [],
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
  {
    id: 'st-in_review',
    project_id: null,
    name: '评审中',
    category: 'in_review',
    color: '#d97706',
    position: 2,
    is_default: false,
    allowed_transitions: [],
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
  {
    id: 'st-done',
    project_id: null,
    name: '已完成',
    category: 'done',
    color: '#16a34a',
    position: 3,
    is_default: false,
    allowed_transitions: [],
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
];

const ISSUE_CHILDREN = [
  {
    id: 'issue-2',
    workspace_id: WORKSPACE_ID,
    project_id: null,
    project: null,
    identifier_namespace_key: 'MESH',
    number: 2,
    identifier: 'MESH-2',
    title: '暗色对比度自证',
    status: null,
    status_id: 'st-done',
    state_category: 'done',
    priority: 'urgent',
    assignee: { id: 'member-human-1', name: 'Ana', member_type: 'human' },
    assignee_id: 'member-human-1',
    reporter: null,
    reporter_id: null,
    estimate: null,
    estimate_unit: null,
    due_date: null,
    start_date: null,
    milestone_id: null,
    cycle_id: null,
    parent_id: 'issue-1',
    position: 0,
    completed_at: isoAt(7_200_000),
    version: 1,
    created_at: isoAt(0),
    updated_at: isoAt(7_200_000),
    children_progress: { total: 0, done: 0 },
  },
];

const ISSUE_DEPENDENCIES = [
  {
    id: 'dep-1',
    issue_id: 'issue-1',
    depends_on_id: 'issue-3',
    depends_on_identifier: 'MESH-3',
    type: 'blocked_by',
    created_by: 'member-human-1',
    created_at: isoAt(0),
  },
];

const ISSUE_ACTIVITY = [
  {
    id: 'act-1',
    issue_id: 'issue-1',
    actor: { id: 'member-human-1', name: 'Ana', member_type: 'human' },
    field: 'status',
    old_value: 'todo',
    new_value: 'in_progress',
    created_at: isoAt(3_600_000),
  },
];

const PROJECTS = [
  { id: 'project-1', name: '平台', key: 'PLAT', created_at: isoAt(0), updated_at: isoAt(0) },
];

const CYCLES = [{ id: 'cycle-1', name: 'Sprint 1', created_at: isoAt(0), updated_at: isoAt(0) }];

const ISSUE_COMMENTS = [
  {
    id: 'c-1',
    issue_id: 'issue-1',
    parent_id: null,
    thread_root_id: null,
    author_kind: 'member',
    author: { id: 'member-human-1', member_type: 'human', name: 'Ana' },
    body_markdown: '开始处理令牌梳理。',
    body_html: '<p>开始处理令牌梳理。</p>',
    body_text: '开始处理令牌梳理。',
    reactions: [],
    reply_count: 0,
    resolved_at: null,
    resolved_by: null,
    mentions: [],
    triggered_execution_ids: [],
    deleted_at: null,
    created_at: isoAt(3_600_000),
    updated_at: isoAt(3_600_000),
    edited_at: null,
  },
];

const ISSUE_LABELS = [
  {
    id: 'lbl-1',
    workspace_id: WORKSPACE_ID,
    project_id: null,
    name: 'theme',
    color: '#7c3aed',
    description: null,
    scope: 'workspace',
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
];

const WORKSPACE_LABELS = [
  {
    id: 'lbl-1',
    workspace_id: WORKSPACE_ID,
    project_id: null,
    name: 'theme',
    color: '#7c3aed',
    description: null,
    scope: 'workspace',
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
  {
    id: 'lbl-2',
    workspace_id: WORKSPACE_ID,
    project_id: null,
    name: 'a11y',
    color: '#0891b2',
    description: null,
    scope: 'workspace',
    created_at: isoAt(0),
    updated_at: isoAt(0),
  },
];

// ---------------------------------------------------------------------------
// 聊天(ChatPage)
// ---------------------------------------------------------------------------

const CHAT_AGENTS = [{ id: 'agent-1', name: 'Mesh Agent', display_name: 'Mesh Agent' }];

const CHAT_SESSIONS = [
  {
    id: 'sess-1',
    workspace_id: WORKSPACE_ID,
    owner_id: 'member-human-1',
    agent_id: 'agent-1',
    agent: { id: 'agent-1', name: 'Mesh Agent', avatar_url: null },
    title: '主题方案讨论',
    title_is_auto: false,
    context_issue_id: null,
    context_project_id: null,
    status: 'active',
    pinned: false,
    last_message_at: isoAt(5_400_000),
    last_message_preview: '好的,我来实现暗色令牌。',
    message_count: 2,
    created_at: isoAt(0),
    updated_at: isoAt(5_400_000),
  },
];

// 时间倒序返回(UI 反转为正序展示)。
const CHAT_MESSAGES = [
  {
    id: 'msg-2',
    session_id: 'sess-1',
    role: 'agent',
    content: '好的,我来实现暗色令牌。',
    generation_id: null,
    generation_status: 'done',
    parent_id: null,
    selected_candidate: false,
    quote_message_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    error_message: null,
    started_at: null,
    finished_at: isoAt(5_400_000),
    created_at: isoAt(5_400_000),
    attachments: [],
    candidate_count: null,
    candidate_index: null,
  },
  {
    id: 'msg-1',
    session_id: 'sess-1',
    role: 'user',
    content: '请实现暗色主题令牌。',
    generation_id: null,
    generation_status: 'done',
    parent_id: null,
    selected_candidate: false,
    quote_message_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    error_message: null,
    started_at: null,
    finished_at: null,
    created_at: isoAt(3_600_000),
    attachments: [],
    candidate_count: null,
    candidate_index: null,
  },
];

// ---------------------------------------------------------------------------
// 运行详情(ExecutionDetailPage,exec-1;终态 completed,无实时流)
// ---------------------------------------------------------------------------

// 字段集与后端 get_execution / `_render_execution` + `_render_attempt` 逐项对齐:
// 后端不返回 agent_name / issue_identifier(无联表展示名),mock 同样不提供;
// 凭证仅元信息,值恒为 '***'(§4.10 红线)。
const EXECUTION_DETAIL = {
  id: 'exec-1',
  workspace_id: 'ws-1',
  agent_id: 'agent-1',
  issue_id: 'issue-1',
  trigger: 'manual',
  status: 'completed',
  priority: 0,
  task_spec: { kind: 'issue_assignment', untrusted_context: {} },
  label_requirements: {},
  required_capabilities: [],
  config_snapshot: {},
  max_attempts: 1,
  queued_at: isoAt(0),
  finished_at: isoAt(120_000),
  timeout_seconds: 600,
  failure_reason: null,
  result: { summary: '完成暗色令牌生成。' },
  cancel_requested_at: null,
  attempts: [
    {
      id: 'att-1',
      attempt_number: 1,
      runtime_id: 'rt-1',
      runtime_name: 'runner-01',
      status: 'completed',
      lease_seq: 1,
      claimed_at: isoAt(1_000),
      started_at: isoAt(2_000),
      finished_at: isoAt(120_000),
      working_branch: 'mesh/exec-1',
      failure_reason: null,
      result: { summary: '完成' },
    },
  ],
  retry_count: 0,
  credentials: [
    {
      id: 'cred-1',
      name: 'GITHUB_TOKEN',
      kind: 'repo_token',
      attempt_id: 'att-1',
      injected_at: isoAt(2_000),
      revoked_at: null,
      value: '***',
    },
  ],
};

const EXECUTION_LOGS = {
  lines: [
    { stream: 'stdout', offset: 0, line: '$ mesh gen:tokens' },
    { stream: 'stdout', offset: 1, line: 'tokens.css written' },
    { stream: 'stdout', offset: 2, line: 'tokens-dark.css written' },
    { stream: 'stdout', offset: 3, line: 'Done.' },
  ],
  next_offset: 4,
};

// ---------------------------------------------------------------------------
// 收件箱(InboxPage)
// ---------------------------------------------------------------------------

const NOTIFICATIONS = [
  {
    id: 'n-1',
    type: 'assigned',
    priority: 'normal',
    issue_id: 'issue-1',
    comment_id: null,
    execution_id: null,
    group_key: null,
    actor: { id: 'member-human-1', member_type: 'human', name: 'Ana' },
    preview: 'Ana 将 MESH-1 指派给你',
    title: '新指派',
    count: 1,
    read_at: null,
    archived_at: null,
    created_at: isoAt(3_600_000),
    latest_comment_id: null,
    issue: { id: 'issue-1', identifier: 'MESH-1', title: '设计系统令牌梳理' },
  },
  {
    id: 'n-2',
    type: 'mentioned',
    priority: 'critical',
    issue_id: 'issue-1',
    comment_id: 'c-1',
    execution_id: null,
    group_key: null,
    actor: { id: 'member-human-1', member_type: 'human', name: 'Ana' },
    preview: 'Ana 在 MESH-1 提到了你',
    title: '提及',
    count: 1,
    read_at: null,
    archived_at: null,
    created_at: isoAt(5_400_000),
    latest_comment_id: 'c-1',
    issue: { id: 'issue-1', identifier: 'MESH-1', title: '设计系统令牌梳理' },
  },
  {
    id: 'n-3',
    type: 'execution_finished',
    priority: 'normal',
    issue_id: 'issue-2',
    comment_id: null,
    execution_id: 'exec-1',
    group_key: null,
    actor: { id: 'agent-1', member_type: 'agent', name: 'Mesh Agent' },
    preview: '执行 exec-1 已完成',
    title: '执行完成',
    count: 1,
    read_at: isoAt(7_000_000),
    archived_at: null,
    created_at: isoAt(7_000_000),
    latest_comment_id: null,
    issue: { id: 'issue-2', identifier: 'MESH-2', title: '暗色对比度自证' },
  },
];

// ---------------------------------------------------------------------------
// 其余 §13.5 核心页(工作台 / issue 列表 / 自动值守 / 集成 / 洞察)
// ---------------------------------------------------------------------------

const AUTOPILOTS = [
  {
    id: 'autopilot-1',
    workspace_id: WORKSPACE_ID,
    name: '每日待办巡检',
    description: '每天检查逾期工作项并通知负责人。',
    trigger_type: 'schedule',
    trigger_config: { cron: '0 9 * * 1-5', timezone: 'UTC' },
    filter_config: {},
    action_config: [{ type: 'send_notification', message: '请检查逾期工作项' }],
    executor_agent_id: null,
    status: 'active',
    guardrails: {
      rate_limit_overflow: 'queue',
      dedup_window_seconds: 300,
      dedup_key_template: '{{issue.id}}',
      daily_run_budget: 100,
      daily_token_budget: 100000,
      approval_required_actions: [],
      kill_switch_paused: false,
      agent_loop_detection: true,
      cascade_max_depth: 3,
      agent_loop_window_seconds: 600,
    },
    max_retries: 3,
    retry_backoff: 'exponential',
    retry_base_seconds: 5,
    retry_max_seconds: 300,
    rate_limit_max: 20,
    rate_limit_window_seconds: 60,
    concurrency_limit: 1,
    require_approval: false,
    next_run_at: isoAt(86_400_000),
    last_run_at: isoAt(3_600_000),
    last_run_status: 'succeeded',
    created_by: 'member-human-1',
    created_at: isoAt(0),
    updated_at: isoAt(3_600_000),
    stats: { runs_30d: 22, success_rate: 0.95 },
  },
];

const INTEGRATIONS = [
  {
    id: 'integration-1',
    workspace_id: WORKSPACE_ID,
    kind: 'vcs_github',
    name: '代码托管',
    status: 'active',
    config: { organization: 'acme' },
    has_secret: true,
    health_state: 'healthy',
    last_error: null,
    last_success_at: isoAt(7_200_000),
    events_7d: 128,
    created_by: 'member-human-1',
    created_at: isoAt(0),
    updated_at: isoAt(7_200_000),
  },
];

const WORKSPACE_DASHBOARD = {
  throughput: {
    granularity: 'day',
    series: [
      {
        label: '07-24',
        bucket: '2026-07-24',
        window_start: '2026-07-24T00:00:00Z',
        window_end: '2026-07-25T00:00:00Z',
        created: 8,
        completed: 5,
        net: 3,
      },
      {
        label: '07-25',
        bucket: '2026-07-25',
        window_start: '2026-07-25T00:00:00Z',
        window_end: '2026-07-26T00:00:00Z',
        created: 4,
        completed: 7,
        net: -3,
      },
    ],
    meta: { calendar_timezone: 'UTC', net_window: 0 },
  },
  workload: {
    data: [
      {
        member_id: 'member-human-1',
        display_name: 'Ana',
        member_type: 'human',
        open_issues: 6,
        running: null,
        queued: null,
        awaiting_approval: null,
      },
      {
        member_id: 'member-agent-1',
        display_name: 'Mesh Agent',
        member_type: 'agent',
        open_issues: 3,
        running: 1,
        queued: 2,
        awaiting_approval: 0,
      },
    ],
    next_cursor: null,
  },
  agent_stats: {
    agents: [
      {
        agent_id: 'agent-1',
        display_name: 'Mesh Agent',
        member_type: 'agent',
        executions: 20,
        succeeded: 18,
        terminal: 20,
        cancelled_count: 0,
        success_rate: 0.9,
        timeout_rate: 0.05,
        avg_duration_seconds: 42,
        retry_rate: 0.1,
        tokens: {
          prompt_tokens: 24000,
          completion_tokens: 8000,
          total_tokens: 32000,
          token_coverage: 1,
        },
        meta: { token_note: 'autopilot executions' },
      },
    ],
    meta: {},
  },
  meta: { visibility_filtered: false, display_timezone: 'UTC' },
};

// ---------------------------------------------------------------------------
// 路由匹配
// ---------------------------------------------------------------------------

/**
 * 处理页面专有数据路由。返回 true 表示已响应;false 表示未命中(交回主服务)。
 * 仅处理 kind === 'general'(外壳引导路由由 mock-server-visual.mjs 直接处理)。
 */
export function handlePageRoute(req, res, url, ctx) {
  if (ctx.kind !== 'general' || req.method !== 'GET') {
    return false;
  }
  const path = url.pathname;

  // ---- issue by-identifier 契约(search-command-palette.md §3.4)---------
  // 规范深链 `/issues/by-identifier/{KEY-N}` 的解析端点:identifier 归一大写匹配,
  // 命中返回主键 UUID 形态(直达详情,杜绝 identifier 形态 id 的二次重定向自循环)。
  const byIdentifierMatch = path.match(
    new RegExp(`^/api/v1/workspaces/${WORKSPACE_ID}/issues/by-identifier/([^/]+)$`),
  );
  if (byIdentifierMatch !== null) {
    const identifier = decodeURIComponent(byIdentifierMatch[1]).toUpperCase();
    if (identifier === ISSUE_DETAIL.identifier) {
      sendJson(res, 200, single({ ...ISSUE_DETAIL, id: ISSUE_UUID }));
    } else {
      sendJson(res, 404, { error: { code: 'not_found', message: 'issue not found' } });
    }
    return true;
  }

  // UUID 规范主键形态与 issue-1 端点集等价(同一 issue 的两种寻址,§5.1)。
  const effectivePath = path
    .replaceAll(`/api/v1/issues/${ISSUE_UUID}`, '/api/v1/issues/issue-1')
    .replace(
      `/squads/assignments/by-issue/${ISSUE_UUID}`,
      '/squads/assignments/by-issue/issue-1',
    );

  // ---- 看板 -------------------------------------------------------------
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/views`) {
    sendJson(res, 200, list([VIEW]));
    return true;
  }
  if (path === '/api/v1/views/view-1/issues') {
    sendJson(res, 200, BOARD_GROUPS);
    return true;
  }

  // ---- issue 详情(issue-1 / UUID 规范形态等价)--------------------------
  if (effectivePath === '/api/v1/issues/issue-1') {
    // UUID 形态寻址回包主键与请求一致(§5.1:同一 issue,寻址形态保真)。
    const detail =
      path === `/api/v1/issues/${ISSUE_UUID}` ? { ...ISSUE_DETAIL, id: ISSUE_UUID } : ISSUE_DETAIL;
    sendJson(res, 200, single(detail));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/statuses`) {
    sendJson(res, 200, list(ISSUE_STATUSES));
    return true;
  }
  if (effectivePath === '/api/v1/issues/issue-1/children') {
    sendJson(res, 200, list(ISSUE_CHILDREN));
    return true;
  }
  if (effectivePath === '/api/v1/issues/issue-1/dependencies') {
    sendJson(res, 200, list(ISSUE_DEPENDENCIES));
    return true;
  }
  if (effectivePath === '/api/v1/issues/issue-1/activity') {
    sendJson(res, 200, list(ISSUE_ACTIVITY));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/projects`) {
    sendJson(res, 200, list(PROJECTS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/cycles`) {
    sendJson(res, 200, list(CYCLES));
    return true;
  }
  if (effectivePath === '/api/v1/issues/issue-1/attachments') {
    sendJson(res, 200, list([]));
    return true;
  }
  if (effectivePath === '/api/v1/issues/issue-1/comments') {
    sendJson(res, 200, list(ISSUE_COMMENTS));
    return true;
  }
  if (effectivePath === '/api/v1/issues/issue-1/labels') {
    sendJson(res, 200, list(ISSUE_LABELS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/labels`) {
    sendJson(res, 200, list(WORKSPACE_LABELS));
    return true;
  }
  if (effectivePath === '/api/v1/issues/issue-1/custom-field-values') {
    sendJson(res, 200, list([]));
    return true;
  }
  if (effectivePath === `/api/v1/workspaces/${WORKSPACE_ID}/squads/assignments/by-issue/issue-1`) {
    sendJson(res, 200, single(null));
    return true;
  }

  // ---- 聊天 -------------------------------------------------------------
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/agents`) {
    sendJson(res, 200, list(CHAT_AGENTS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/chat-sessions`) {
    sendJson(res, 200, list(CHAT_SESSIONS));
    return true;
  }
  if (path === '/api/v1/favorites') {
    sendJson(res, 200, list([]));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/chat-sessions/sess-1/messages`) {
    sendJson(res, 200, list(CHAT_MESSAGES));
    return true;
  }

  // ---- 运行详情(exec-1)------------------------------------------------
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/executions/exec-1`) {
    sendJson(res, 200, single(EXECUTION_DETAIL));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/executions/exec-1/logs`) {
    sendJson(res, 200, single(EXECUTION_LOGS));
    return true;
  }

  // ---- 收件箱 -----------------------------------------------------------
  if (path === '/api/v1/inbox') {
    sendJson(res, 200, list(NOTIFICATIONS));
    return true;
  }

  // ---- 工作台 -----------------------------------------------------------
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/executions`) {
    sendJson(res, 200, list([]));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/approvals`) {
    sendJson(res, 200, list([]));
    return true;
  }

  // ---- 自动值守 ---------------------------------------------------------
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilots/kill-switch`) {
    sendJson(res, 200, single({ kill_switch: false }));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilots`) {
    sendJson(res, 200, list(AUTOPILOTS));
    return true;
  }

  // ---- 集成 -------------------------------------------------------------
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/integrations`) {
    sendJson(res, 200, list(INTEGRATIONS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/integrations/integration-1/bindings`) {
    sendJson(res, 200, list([]));
    return true;
  }

  // ---- 洞察 -------------------------------------------------------------
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/dashboards/workspace`) {
    sendJson(res, 200, single(WORKSPACE_DASHBOARD));
    return true;
  }

  return false;
}
