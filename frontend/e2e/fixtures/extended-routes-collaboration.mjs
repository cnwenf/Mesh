/**
 * Deterministic browser fixtures for the collaboration route families that are
 * outside the visual-regression core set. The response envelopes mirror the
 * public API contract: objects use `{ data }` and collections additionally use
 * `next_cursor`.
 */

const BASE_TIME = Date.UTC(2026, 6, 25, 8, 0, 0);
const WORKSPACE_ID = 'ws-1';

function isoAt(offsetMs = 0) {
  return new Date(BASE_TIME + offsetMs).toISOString();
}

function corsHeaders(extra = {}) {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers':
      'Authorization, Content-Type, If-Match, Idempotency-Key, If-None-Match, Last-Event-ID',
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

function sendEventStream(res) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-store',
    ...corsHeaders(),
  });
  // A complete keepalive-only stream lets the task page establish and close
  // its pre-tree connection without creating a synthetic orchestration event.
  res.end(': fixture complete\n\n');
}

const single = (data) => ({ data });
const list = (data) => ({ data, next_cursor: null });

const HUMAN = { id: 'member-human-1', name: 'Ana', member_type: 'human' };
const AGENT_MEMBER = { member_id: 'member-agent-1', member_type: 'agent', name: 'Mesh Agent' };

const MILESTONE = {
  id: 'milestone-1',
  project_id: 'project-1',
  title: 'Accessibility release',
  description: 'Complete the keyboard and screen-reader acceptance pass.',
  target_date: '2026-08-15',
  state: 'open',
  overdue: false,
  created_at: isoAt(),
  updated_at: isoAt(3_600_000),
};

const PROJECT = {
  id: 'project-1',
  workspace_id: WORKSPACE_ID,
  name: 'Platform quality',
  key: 'MESH',
  description: 'Build a fast, inclusive collaboration workspace.',
  icon: '◇',
  color: '#2563eb',
  status: 'active',
  health: 'on_track',
  visibility: 'public',
  lead: HUMAN,
  lead_member_id: HUMAN.id,
  start_date: '2026-07-01',
  target_date: '2026-08-31',
  progress: 0.6,
  open_issues: 4,
  done_issues: 6,
  issue_seq: 10,
  archived: false,
  archived_at: null,
  my_role: 'lead',
  created_at: isoAt(),
  updated_at: isoAt(3_600_000),
  milestones: [MILESTONE],
};

const PROJECT_UPDATES = [
  {
    id: 'project-update-1',
    project_id: PROJECT.id,
    author: HUMAN,
    health: 'on_track',
    status: 'active',
    message: 'Keyboard acceptance flow is ready for verification.',
    created_at: isoAt(7_200_000),
  },
];

const PROJECT_MEMBERS = [
  {
    id: 'project-member-1',
    project_id: PROJECT.id,
    member_id: HUMAN.id,
    member: HUMAN,
    role: 'lead',
    created_at: isoAt(),
  },
];

const PROJECT_LABELS = [
  {
    id: 'label-project-a11y',
    workspace_id: WORKSPACE_ID,
    project_id: PROJECT.id,
    name: 'accessibility',
    color: '#2563eb',
    description: 'Accessibility acceptance work',
    scope: 'project',
    created_at: isoAt(),
    updated_at: isoAt(),
  },
];

const ISSUE_STATUS = {
  id: 'status-in-progress',
  project_id: null,
  name: 'In progress',
  category: 'in_progress',
  color: '#2563eb',
  position: 1,
  is_default: false,
  allowed_transitions: [],
  created_at: isoAt(),
  updated_at: isoAt(),
};

const IDENTIFIER_ISSUE = {
  id: 'issue-1',
  workspace_id: WORKSPACE_ID,
  project_id: PROJECT.id,
  project: { id: PROJECT.id, name: PROJECT.name, key: PROJECT.key },
  identifier_namespace_key: 'MESH',
  number: 1,
  identifier: 'MESH-1',
  title: 'Keyboard acceptance flow',
  description: 'Exercise the issue flow without a pointer.',
  status: ISSUE_STATUS,
  status_id: ISSUE_STATUS.id,
  state_category: 'in_progress',
  priority: 'high',
  assignee: HUMAN,
  assignee_id: HUMAN.id,
  reporter: HUMAN,
  reporter_id: HUMAN.id,
  estimate: 3,
  estimate_unit: 'points',
  due_date: '2026-08-15',
  start_date: '2026-07-25',
  milestone_id: MILESTONE.id,
  cycle_id: null,
  parent_id: null,
  position: 0,
  completed_at: null,
  version: 2,
  created_at: isoAt(),
  updated_at: isoAt(7_200_000),
  children_progress: { total: 0, done: 0 },
};

const AGENT = {
  id: 'agent-1',
  member: {
    id: 'member-agent-1',
    member_type: 'agent',
    display_name: 'Mesh Agent',
    avatar_url: null,
    role_tag: 'Engineer',
    role: 'member',
    status: 'active',
  },
  display_name: 'Mesh Agent',
  name: 'Mesh Agent',
  avatar_url: null,
  role_tag: 'Engineer',
  badge_kind: 'ai',
  lifecycle_status: 'active',
  visibility: 'workspace',
  trigger_on_assign: true,
  owner_user_id: 'user-1',
  created_at: isoAt(),
  updated_at: isoAt(3_600_000),
  slug: 'mesh-agent',
  bio: 'Builds and verifies collaboration features.',
  system_instructions: 'Keep changes focused and verify behavior end to end.',
  model_config: {
    model: 'mainstream-llm-balanced',
    model_tier: 'balanced',
    temperature: 0.2,
    top_p: 1,
    max_tokens: 8192,
    reasoning_effort: 'medium',
    preset: 'strict_engineering',
  },
  default_runtime_id: null,
  active_config_version_id: 'agent-config-2',
  current_version: {
    id: 'agent-config-2',
    change_summary: 'Tighten verification guidance',
    changed_by: HUMAN.id,
    created_at: isoAt(3_600_000),
  },
};

const AGENT_STATS = {
  agent_id: AGENT.id,
  display_name: AGENT.display_name,
  member_type: 'agent',
  executions: 20,
  succeeded: 19,
  terminal: 20,
  cancelled_count: 0,
  success_rate: 0.95,
  timeout_rate: 0,
  avg_duration_seconds: 38,
  retry_rate: 0.05,
  tokens: {
    prompt_tokens: 24_000,
    completion_tokens: 8_000,
    total_tokens: 32_000,
    token_coverage: 1,
  },
  meta: { token_note: 'Autopilot executions with token accounting' },
};

const AGENT_CONFIG_VERSIONS = [
  {
    id: 'agent-config-2',
    agent_id: AGENT.id,
    snapshot: {
      system_instructions: AGENT.system_instructions,
      model_config: AGENT.model_config,
      skill_versions: { 'skill-1': 'skill-version-1' },
      capability_grants: [],
    },
    change_summary: 'Tighten verification guidance',
    changed_by: HUMAN.id,
    created_at: isoAt(3_600_000),
  },
  {
    id: 'agent-config-1',
    agent_id: AGENT.id,
    snapshot: {
      system_instructions: 'Build collaboration features.',
      model_config: { model_tier: 'balanced', temperature: 0.2 },
      skill_versions: {},
      capability_grants: [],
    },
    change_summary: 'Initial configuration',
    changed_by: HUMAN.id,
    created_at: isoAt(),
  },
];

const SQUAD_LEADER = { member_id: HUMAN.id, member_type: 'human', name: HUMAN.name };

const SQUAD = {
  id: 'squad-1',
  workspace_id: WORKSPACE_ID,
  name: 'Quality crew',
  description: 'Coordinates release verification across the product.',
  instructions: 'Keep acceptance evidence concise and reproducible.',
  avatar_url: null,
  kind: 'standing',
  status: 'active',
  leader_mode: 'single',
  primary_leader_id: HUMAN.id,
  primary_leader: SQUAD_LEADER,
  require_plan_approval: false,
  max_decompose_depth: 2,
  member_count: 2,
  active_task_count: 0,
  leaders: [SQUAD_LEADER],
  member_preview: [
    { ...SQUAD_LEADER, role: 'leader' },
    { ...AGENT_MEMBER, role: 'member' },
  ],
  archived_at: null,
  created_at: isoAt(),
  updated_at: isoAt(7_200_000),
};

const SQUAD_MEMBERS = [
  {
    id: 'squad-member-1',
    ...SQUAD_LEADER,
    role: 'leader',
    joined_at: isoAt(),
  },
  {
    id: 'squad-member-2',
    ...AGENT_MEMBER,
    role: 'member',
    joined_at: isoAt(60_000),
  },
];

function squadTask(overrides = {}) {
  return {
    id: 'task-1',
    squad_id: SQUAD.id,
    issue_id: IDENTIFIER_ISSUE.id,
    parent_task_id: null,
    root_task_id: null,
    depth: 0,
    title_snapshot: 'Verify the keyboard acceptance flow',
    status: 'done',
    assignee: AGENT_MEMBER,
    stage: null,
    execution_id: 'execution-1',
    plan_markdown: '1. Exercise the route.\n2. Record the result.',
    result_summary: 'All automated collaboration checks passed.',
    failure_reason: null,
    depends_on: [],
    blocked_by: [],
    dispatched_at: isoAt(60_000),
    started_at: isoAt(120_000),
    finished_at: isoAt(3_600_000),
    created_at: isoAt(),
    updated_at: isoAt(3_600_000),
    ...overrides,
  };
}

const SQUAD_TASK_CHILD = squadTask({
  id: 'task-checklist',
  parent_task_id: 'task-1',
  root_task_id: 'task-1',
  depth: 1,
  title_snapshot: 'Run the acceptance checklist',
  stage: 1,
  execution_id: 'execution-2',
});

const SQUAD_TASK = squadTask();
const SQUAD_TASK_TREE = {
  ...SQUAD_TASK,
  children: [SQUAD_TASK_CHILD],
  progress: { total: 1, done: 1, in_progress: 0, pending: 0, failed: 0 },
};

const SQUAD_ACTIVITY = [
  {
    id: 'squad-activity-1',
    task_id: SQUAD_TASK.id,
    actor_kind: 'member',
    actor: AGENT_MEMBER,
    action: 'task_completed',
    target_type: 'task',
    target_id: SQUAD_TASK.id,
    payload: { result: 'passed' },
    created_at: isoAt(3_600_000),
  },
];

const SQUAD_MESSAGES = [
  {
    id: 'squad-message-1',
    squad_id: SQUAD.id,
    task_id: SQUAD_TASK.id,
    sender: AGENT_MEMBER,
    recipient: SQUAD_LEADER,
    kind: 'report',
    body_markdown: 'The collaboration route checks are green.',
    body_html: '<p>The collaboration route checks are green.</p>',
    pinned: false,
    attachment_ids: [],
    created_at: isoAt(3_600_000),
  },
];

const SKILL = {
  id: 'skill-1',
  workspace_id: WORKSPACE_ID,
  source_id: 'source-quality-checklist',
  source_type: 'user',
  trust_level: 'reviewed',
  name: 'Release verification',
  slug: 'release-verification',
  summary: 'Runs the repeatable release acceptance checklist.',
  status: 'published',
  current_version_id: 'skill-version-1',
  current_version: '1.0.0',
  has_scripts: true,
  install_status: 'installed',
  required_capabilities: ['read:workspace'],
  tags: ['quality', 'release'],
  icon: null,
  created_by: HUMAN.id,
  created_at: isoAt(),
  updated_at: isoAt(3_600_000),
};

const SKILL_VERSION = {
  id: 'skill-version-1',
  skill_id: SKILL.id,
  version: '1.0.0',
  instructions: 'Run the acceptance checklist and report only reproducible results.',
  status: 'published',
  changelog: 'Initial verified workflow',
  io_contract: null,
  required_capabilities: ['read:workspace'],
  content_hash: 'a'.repeat(64),
  created_by: HUMAN.id,
  created_at: isoAt(),
  is_current: true,
  scripts: [
    {
      id: 'skill-script-1',
      path: 'scripts/verify.sh',
      runtime: 'shell',
      entrypoint: true,
      content_ref: 'fixture:verify',
      content_hash: 'b'.repeat(64),
      required_capabilities: ['read:workspace'],
      content: '#!/bin/sh\nprintf "verified\\n"',
    },
  ],
  references: [
    {
      id: 'skill-reference-1',
      path: 'docs/verification.md',
      media_type: 'text/markdown',
      content_ref: 'fixture:verification',
      summary: 'Verification runbook',
    },
  ],
  triggers: [
    {
      id: 'skill-trigger-1',
      trigger_type: 'keyword',
      pattern: 'verify release',
      weight: 1,
    },
  ],
};

const SKILL_INSTALLATION = {
  id: 'skill-installation-1',
  workspace_id: WORKSPACE_ID,
  skill_id: SKILL.id,
  skill_version_id: SKILL_VERSION.id,
  scope: 'workspace',
  agent_id: null,
  install_status: 'installed',
  auto_update: false,
  granted_capabilities: ['read:workspace'],
  installed_by: HUMAN.id,
  installed_at: isoAt(60_000),
  created_at: isoAt(60_000),
  updated_at: isoAt(60_000),
};

const AGENT_SKILLS = [
  {
    binding_id: 'agent-skill-binding-1',
    skill: {
      id: SKILL.id,
      name: SKILL.name,
      slug: SKILL.slug,
      summary: SKILL.summary,
      source_type: SKILL.source_type,
      trust_level: SKILL.trust_level,
      status: SKILL.status,
    },
    skill_version_id: SKILL_VERSION.id,
    version: SKILL_VERSION.version,
    install_status: SKILL_INSTALLATION.install_status,
    enabled: true,
    auto_trigger: false,
    priority: 100,
  },
];

const MARKETPLACE_SKILLS = [
  {
    id: 'market-skill-1',
    name: 'Incident triage',
    summary: 'Provides a structured incident triage workflow.',
    version: '2.1.0',
    manifest_url: 'https://skills.example.test/incident-triage/manifest.json',
    downloads: 1240,
    rating: 4.8,
    certified: true,
    has_scripts: false,
    tags: ['incident', 'operations'],
  },
];

const PROJECT_DASHBOARD = {
  project_id: PROJECT.id,
  velocity: { cycles: [], meta: { scope_caliber: 'current_attribution' } },
  burndown: null,
  cycle_time: {
    project_id: PROJECT.id,
    from_category: 'in_progress',
    p50_seconds: 3600,
    p90_seconds: 7200,
    sample_size: 6,
    meta: { insufficient_data: 0 },
  },
};

/**
 * Handle project, identifier, agent, squad, and skill browser fixtures.
 * Returns true after writing a response and false when the route belongs to a
 * different fixture family.
 */
export function handleExtendedCollaborationRoute(req, res, url) {
  if (req.method !== 'GET') return false;

  const path = url.pathname;

  // Projects: detail, settings auxiliaries, and the optional dashboard tab.
  if (path === `/api/v1/projects/${PROJECT.id}`) {
    sendJson(res, 200, single(PROJECT));
    return true;
  }
  if (path === `/api/v1/projects/${PROJECT.id}/updates`) {
    sendJson(res, 200, list(PROJECT_UPDATES));
    return true;
  }
  if (path === `/api/v1/projects/${PROJECT.id}/members`) {
    sendJson(res, 200, list(PROJECT_MEMBERS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/labels`) {
    sendJson(res, 200, list(PROJECT_LABELS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/custom-fields`) {
    sendJson(res, 200, list([]));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/dashboards/project/${PROJECT.id}`) {
    sendJson(res, 200, single(PROJECT_DASHBOARD));
    return true;
  }

  // Both identifier resolver routes call the same workspace-scoped endpoint.
  if (
    path ===
    `/api/v1/workspaces/${WORKSPACE_ID}/issues/by-identifier/${IDENTIFIER_ISSUE.identifier}`
  ) {
    sendJson(res, 200, single(IDENTIFIER_ISSUE));
    return true;
  }
  if (path === `/api/v1/issues/${IDENTIFIER_ISSUE.id}/vcs-links`) {
    sendJson(res, 200, list([]));
    return true;
  }

  // Agent overview plus the GETs used by its history and skills tabs.
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/agents/${AGENT.id}`) {
    sendJson(res, 200, single(AGENT));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/analytics/agents/stats`) {
    sendJson(res, 200, single(AGENT_STATS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/agents/${AGENT.id}/config-versions`) {
    sendJson(res, 200, list(AGENT_CONFIG_VERSIONS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/agents/${AGENT.id}/skills`) {
    sendJson(res, 200, list(AGENT_SKILLS));
    return true;
  }

  // Squad list/detail and its five independently loaded detail collections.
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/squads`) {
    sendJson(res, 200, list([SQUAD]));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}`) {
    sendJson(res, 200, single(SQUAD));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}/members`) {
    sendJson(res, 200, list(SQUAD_MEMBERS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}/tasks`) {
    sendJson(res, 200, list([SQUAD_TASK]));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}/activity`) {
    sendJson(res, 200, list(SQUAD_ACTIVITY));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}/messages`) {
    sendJson(res, 200, list(SQUAD_MESSAGES));
    return true;
  }
  if (
    path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}/tasks/${SQUAD_TASK.id}/tree`
  ) {
    sendJson(res, 200, single(SQUAD_TASK_TREE));
    return true;
  }
  if (
    path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}/tasks/${SQUAD_TASK.id}/status`
  ) {
    sendJson(
      res,
      200,
      single({
        task_id: SQUAD_TASK.id,
        status: SQUAD_TASK.status,
        result_summary: SQUAD_TASK.result_summary,
      }),
    );
    return true;
  }
  if (
    path === `/api/v1/workspaces/${WORKSPACE_ID}/squads/${SQUAD.id}/tasks/${SQUAD_TASK.id}/stream`
  ) {
    sendEventStream(res);
    return true;
  }

  // Skills library, marketplace, detail/version data, and shared installation data.
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/marketplace/skills`) {
    sendJson(res, 200, list(MARKETPLACE_SKILLS));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/skills`) {
    sendJson(res, 200, list([SKILL]));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/skills/${SKILL.id}`) {
    sendJson(res, 200, single(SKILL));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/skills/${SKILL.id}/versions`) {
    sendJson(res, 200, list([SKILL_VERSION]));
    return true;
  }
  if (
    path === `/api/v1/workspaces/${WORKSPACE_ID}/skills/${SKILL.id}/versions/${SKILL_VERSION.id}`
  ) {
    sendJson(res, 200, single(SKILL_VERSION));
    return true;
  }
  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/skill-installations`) {
    sendJson(res, 200, list([SKILL_INSTALLATION]));
    return true;
  }

  return false;
}
