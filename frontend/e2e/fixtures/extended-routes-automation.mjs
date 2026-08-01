/**
 * 扩展浏览器巡检的自动化与运行时恒定 fixture。
 *
 * 此处理器只补充页面正常态所需的数据路由。时间、标识与内容均为固定值，确保
 * 截图和可访问性巡检不会受当前时间或随机数据影响。响应包络与 API client 约定
 * 保持一致：单对象 `{data}`，列表 `{data,next_cursor}`。
 */

const BASE_TIME = Date.UTC(2026, 6, 25, 8, 0, 0);
const WORKSPACE_ID = 'ws-1';

function isoAt(offsetMs) {
  return new Date(BASE_TIME + offsetMs).toISOString();
}

function corsHeaders(extra = {}) {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers':
      'Authorization, Content-Type, If-Match, Idempotency-Key, If-None-Match',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    'Access-Control-Expose-Headers': 'ETag, Retry-After',
    ...extra,
  };
}

function sendJson(res, status, body) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    ...corsHeaders(),
  });
  res.end(JSON.stringify(body));
}

const single = (data) => ({ data });
const list = (data) => ({ data, next_cursor: null });

const RUNTIME = {
  id: 'runtime-1',
  name: 'intranet-build-01',
  kind: 'self_hosted',
  status: 'online',
  labels: { region: 'intranet', gpu: 'false' },
  capabilities: ['version_control', 'python', 'ffmpeg'],
  hostname: 'build-node-7',
  os: 'linux-x86_64',
  cpu_cores: 8,
  memory_mb: 32768,
  max_concurrent: 4,
  current_load: 1,
  last_heartbeat_at: isoAt(14_395_000),
  heartbeat_interval_seconds: 15,
  version: '1.4.2',
  created_at: isoAt(-15_552_000_000),
  updated_at: isoAt(14_395_000),
};

const RUNTIME_EXECUTIONS = [
  {
    id: 'runtime-exec-running',
    agent_id: 'agent-1',
    issue_id: 'issue-1',
    trigger: 'assign',
    status: 'running',
    priority: 100,
    required_capabilities: ['python'],
    label_requirements: { region: 'intranet' },
    timeout_seconds: 1800,
    queued_at: isoAt(13_800_000),
    finished_at: null,
    failure_reason: null,
    result: null,
    max_attempts: 3,
    attempts: [
      {
        id: 'runtime-attempt-running',
        attempt_number: 1,
        runtime_name: RUNTIME.name,
        runtime_id: RUNTIME.id,
        status: 'running',
        claimed_at: isoAt(13_805_000),
        started_at: isoAt(13_810_000),
        finished_at: null,
        working_branch: 'agent/runtime-exec-running/a1',
        result: null,
        failure_reason: null,
      },
    ],
    cancel_requested_at: null,
    credentials: [],
  },
  {
    id: 'runtime-exec-complete',
    agent_id: 'agent-1',
    issue_id: 'issue-2',
    trigger: 'mention',
    status: 'completed',
    priority: 50,
    required_capabilities: ['version_control'],
    label_requirements: {},
    timeout_seconds: 1800,
    queued_at: isoAt(7_200_000),
    finished_at: isoAt(7_320_000),
    failure_reason: null,
    result: { summary: '检查完成' },
    max_attempts: 3,
    attempts: [
      {
        id: 'runtime-attempt-complete',
        attempt_number: 1,
        runtime_name: RUNTIME.name,
        runtime_id: RUNTIME.id,
        status: 'completed',
        claimed_at: isoAt(7_205_000),
        started_at: isoAt(7_210_000),
        finished_at: isoAt(7_320_000),
        working_branch: 'agent/runtime-exec-complete/a1',
        result: { exit_code: 0 },
        failure_reason: null,
      },
    ],
    cancel_requested_at: null,
    credentials: [],
  },
];

const EDITOR_AGENTS = [
  {
    id: 'agent-1',
    member: {
      id: 'member-agent-1',
      member_type: 'agent',
      display_name: 'Mesh Agent',
      avatar_url: null,
      role_tag: 'assistant',
      role: 'member',
      status: 'active',
    },
    display_name: 'Mesh Agent',
    name: 'Mesh Agent',
    avatar_url: null,
    role_tag: 'assistant',
    badge_kind: 'agent',
    lifecycle_status: 'active',
    visibility: 'workspace',
    trigger_on_assign: true,
    owner_user_id: 'user-1',
    created_at: isoAt(-2_592_000_000),
    updated_at: isoAt(0),
  },
];

const AUTOPILOT = {
  id: 'autopilot-1',
  workspace_id: WORKSPACE_ID,
  name: '每日待办巡检',
  description: '每天检查逾期工作项并通知负责人。',
  trigger_type: 'schedule',
  trigger_config: {
    cron: '0 9 * * 1-5',
    timezone: 'UTC',
    misfire_policy: 'run_once',
  },
  filter_config: {},
  action_config: [{ type: 'send_notification', message: '请检查逾期工作项' }],
  executor_agent_id: null,
  status: 'active',
  guardrails: {
    rate_limit_overflow: 'queue',
    dedup_window_seconds: 300,
    dedup_key_template: '{{trigger.event_id}}',
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
  next_run_at: '2026-07-27T09:00:00.000Z',
  last_run_at: '2026-07-24T09:00:00.000Z',
  last_run_status: 'succeeded',
  created_by: 'member-human-1',
  created_at: isoAt(-2_073_600_000),
  updated_at: isoAt(3_600_000),
  stats: { runs_30d: 22, success_rate: 0.95 },
};

const SCHEDULE_PREVIEW = {
  cron: '0 9 * * 1-5',
  timezone: 'UTC',
  next_runs: [
    '2026-07-27T09:00:00.000Z',
    '2026-07-28T09:00:00.000Z',
    '2026-07-29T09:00:00.000Z',
    '2026-07-30T09:00:00.000Z',
    '2026-07-31T09:00:00.000Z',
  ],
};

const AUTOPILOT_RUN = {
  id: 'run-1',
  autopilot_id: AUTOPILOT.id,
  workspace_id: WORKSPACE_ID,
  trigger_type: 'schedule',
  trigger_snapshot: { scheduled_for: '2026-07-24T09:00:00.000Z' },
  webhook_event_id: null,
  execution_id: null,
  parent_run_id: null,
  cascade_depth: 0,
  status: 'succeeded',
  started_at: '2026-07-24T09:00:00.000Z',
  finished_at: '2026-07-24T09:00:04.000Z',
  duration_ms: 4000,
  retry_count: 0,
  error: null,
  prompt_tokens: 120,
  completion_tokens: 30,
  total_tokens: 150,
  triggered_by: 'member-human-1',
  is_test: false,
  created_at: '2026-07-24T09:00:00.000Z',
  updated_at: '2026-07-24T09:00:04.000Z',
  attempts: [
    {
      attempt_number: 1,
      status: 'succeeded',
      execution_id: null,
      started_at: '2026-07-24T09:00:00.000Z',
      finished_at: '2026-07-24T09:00:04.000Z',
      error: null,
      prompt_tokens: 120,
      completion_tokens: 30,
    },
  ],
  artifacts: [
    {
      id: 'run-artifact-1',
      artifact_type: 'notification',
      ref_table: 'notifications',
      ref_id: 'notification-1',
      summary: '逾期工作项提醒已发送',
      created_at: '2026-07-24T09:00:04.000Z',
    },
  ],
};

const WEBHOOK_SECRETS = [
  {
    id: 'secret-1',
    label: 'default',
    status: 'active',
    created_at: isoAt(-604_800_000),
    revoked_at: null,
  },
];

const WEBHOOK_EVENTS = [
  {
    id: 'webhook-event-1',
    autopilot_id: AUTOPILOT.id,
    idempotency_key: 'evt-20260725-1',
    event_type: 'issue.updated',
    headers: { 'x-source': 'fixture' },
    payload: { issue_id: 'issue-1', status: 'in_progress' },
    signature_status: 'valid',
    process_status: 'processed',
    received_at: isoAt(10_800_000),
  },
];

const INTEGRATION = {
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
  created_at: isoAt(-2_592_000_000),
  updated_at: isoAt(7_200_000),
};

const INTEGRATION_EVENTS = [
  {
    id: 'integration-event-1',
    integration_id: INTEGRATION.id,
    external_event_id: 'github-event-20260725-1',
    event_type: 'pull_request',
    payload: { repository: 'acme/mesh-app', number: 128, action: 'synchronize' },
    signature_status: 'valid',
    process_status: 'processed',
    received_at: isoAt(9_000_000),
  },
];

const WEBHOOK_SUBSCRIPTION = {
  id: 'subscription-1',
  integration_id: INTEGRATION.id,
  url: 'https://hooks.example.test/mesh',
  event_types: ['issue.updated'],
  status: 'active',
  fail_count: 0,
  has_secret: true,
  deliveries_total: 20,
  deliveries_sent: 19,
  success_rate: 0.95,
  created_by: 'member-human-1',
  created_at: isoAt(-2_592_000_000),
  updated_at: isoAt(10_800_000),
};

const WEBHOOK_DELIVERIES = [
  {
    id: 'delivery-1',
    subscription_id: WEBHOOK_SUBSCRIPTION.id,
    event_ref: 'issue.updated:issue-1',
    state: 'sent',
    attempts: 1,
    next_retry_at: null,
    response_status: 200,
    last_error: null,
    created_at: isoAt(10_800_000),
  },
];

/**
 * 处理运行时、自动值守、入站/出向 webhook 与集成详情路由。
 * 返回 true 表示已响应，false 表示未命中，应交回调用方继续分派。
 */
export function handleExtendedAutomationRoute(req, res, url) {
  const path = url.pathname;

  // 自动值守编辑器在新建和编辑态都需要 agent 选项。
  if (req.method === 'GET' && path === `/api/v1/workspaces/${WORKSPACE_ID}/agents`) {
    sendJson(res, 200, list(EDITOR_AGENTS));
    return true;
  }

  if (req.method === 'GET' && path === `/api/v1/workspaces/${WORKSPACE_ID}/runtimes`) {
    sendJson(res, 200, list([RUNTIME]));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/runtimes/${RUNTIME.id}/executions`
  ) {
    sendJson(res, 200, list(RUNTIME_EXECUTIONS));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/runtimes/${RUNTIME.id}`
  ) {
    sendJson(res, 200, single(RUNTIME));
    return true;
  }

  if (
    req.method === 'POST' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilots/preview-schedule`
  ) {
    sendJson(res, 200, single(SCHEDULE_PREVIEW));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilots/${AUTOPILOT.id}/preview-schedule`
  ) {
    sendJson(res, 200, single(SCHEDULE_PREVIEW));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilots/${AUTOPILOT.id}/runs`
  ) {
    sendJson(res, 200, list([AUTOPILOT_RUN]));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilots/${AUTOPILOT.id}`
  ) {
    sendJson(res, 200, single(AUTOPILOT));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilot-runs/${AUTOPILOT_RUN.id}/artifacts`
  ) {
    sendJson(res, 200, list(AUTOPILOT_RUN.artifacts));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/autopilot-runs/${AUTOPILOT_RUN.id}`
  ) {
    sendJson(res, 200, single(AUTOPILOT_RUN));
    return true;
  }

  if (req.method === 'GET' && path === `/api/v1/workspaces/${WORKSPACE_ID}/webhook-secrets`) {
    sendJson(res, 200, list(WEBHOOK_SECRETS));
    return true;
  }
  if (req.method === 'GET' && path === `/api/v1/workspaces/${WORKSPACE_ID}/webhook-events`) {
    sendJson(res, 200, list(WEBHOOK_EVENTS));
    return true;
  }

  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/integrations/${INTEGRATION.id}/bindings`
  ) {
    // 与核心集成列表保持同一无绑定正常态，避免改变其稳定截图。
    sendJson(res, 200, list([]));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/integrations/${INTEGRATION.id}/events`
  ) {
    sendJson(res, 200, list(INTEGRATION_EVENTS));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/integrations/${INTEGRATION.id}`
  ) {
    sendJson(res, 200, single(INTEGRATION));
    return true;
  }

  if (
    req.method === 'GET' &&
    path ===
      `/api/v1/workspaces/${WORKSPACE_ID}/webhook-subscriptions/${WEBHOOK_SUBSCRIPTION.id}/deliveries`
  ) {
    sendJson(res, 200, list(WEBHOOK_DELIVERIES));
    return true;
  }
  if (
    req.method === 'GET' &&
    path === `/api/v1/workspaces/${WORKSPACE_ID}/webhook-subscriptions/${WEBHOOK_SUBSCRIPTION.id}`
  ) {
    sendJson(res, 200, single(WEBHOOK_SUBSCRIPTION));
    return true;
  }
  if (req.method === 'GET' && path === `/api/v1/workspaces/${WORKSPACE_ID}/webhook-subscriptions`) {
    sendJson(res, 200, list([WEBHOOK_SUBSCRIPTION]));
    return true;
  }

  return false;
}
