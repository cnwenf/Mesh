/**
 * Deterministic normal-state fixtures for public account flows and settings routes.
 *
 * The visual mock owns shell bootstrap routes; this handler supplies the data that is
 * specific to OAuth/invitation flows and account/workspace settings. List responses
 * deliberately use the same `{data,next_cursor}` envelope consumed by MeshApiClient.
 */

const FIXTURE_TIME = '2026-07-25T08:00:00.000Z';
const WORKSPACE_ID = 'ws-1';
const USER_ID = 'user-1';
const MEMBER_ID = 'member-human-1';

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
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    ...corsHeaders(),
  });
  res.end(JSON.stringify(body));
}

const single = (data) => ({ data });
const list = (data) => ({ data, next_cursor: null });

const CURRENT_USER = {
  id: USER_ID,
  email: 'ana@mesh.dev',
  email_verified: true,
  display_name: 'Ana',
  avatar_url: null,
  status: 'active',
  timezone: 'UTC',
  settings: { locale: 'zh-CN', theme: 'light' },
  mfa_enabled: false,
  last_login_at: FIXTURE_TIME,
  created_at: FIXTURE_TIME,
  updated_at: FIXTURE_TIME,
};

export const SETTINGS_MEMBERS = [
  {
    id: MEMBER_ID,
    member_type: 'human',
    role: 'owner',
    status: 'active',
    display_name: 'Ana',
    joined_at: FIXTURE_TIME,
    profile: {
      id: USER_ID,
      full_name: 'Ana',
      email: 'ana@mesh.dev',
      avatar_url: null,
    },
  },
  {
    id: 'member-agent-1',
    member_type: 'agent',
    role: 'member',
    status: 'active',
    display_name: 'Release Agent',
    joined_at: FIXTURE_TIME,
    profile: {
      id: 'agent-1',
      name: 'Release Agent',
      description: 'Checks release readiness',
      avatar_url: null,
      is_active: true,
      role_tag: 'reviewer',
      lifecycle_status: 'running',
    },
  },
];

const NOTIFICATION_PREFERENCES = [
  'assigned',
  'mentioned',
  'subscribed_update',
  'comment_created',
  'status_changed',
  'review_requested',
  'due_soon',
  'execution_finished',
].map((eventType, index) => ({
  id: `preference-${String(index + 1)}`,
  event_type: eventType,
  in_app: true,
  email: eventType === 'execution_finished' ? 'realtime' : 'digest',
  quiet_hours_start: '22:00',
  quiet_hours_end: '07:00',
}));

const INVITATION = {
  id: 'invitation-fixture',
  email: 'new.member@example.com',
  role: 'member',
  status: 'active',
  max_uses: 1,
  used_count: 0,
  expires_at: '2026-08-25T08:00:00.000Z',
  token_prefix: 'invite_fixt',
  invited_by: MEMBER_ID,
  created_at: FIXTURE_TIME,
};

const LABEL = {
  id: 'label-fixture',
  workspace_id: WORKSPACE_ID,
  project_id: null,
  name: 'Accessibility',
  color: '#3e63dd',
  description: 'Keyboard and assistive-technology work',
  scope: 'workspace',
  created_at: FIXTURE_TIME,
  updated_at: FIXTURE_TIME,
};

const CUSTOM_FIELD = {
  id: 'custom-field-fixture',
  workspace_id: WORKSPACE_ID,
  project_id: null,
  name: 'Severity',
  field_key: 'severity',
  type: 'single_select',
  is_required: false,
  required_on: [],
  default_value: null,
  config: {},
  position: 0,
  is_active: true,
  options: [
    {
      id: 'custom-field-option-fixture',
      field_def_id: 'custom-field-fixture',
      name: 'Major',
      color: '#f5a623',
      position: 0,
      is_active: true,
      created_at: FIXTURE_TIME,
      updated_at: FIXTURE_TIME,
    },
  ],
  created_at: FIXTURE_TIME,
  updated_at: FIXTURE_TIME,
};

const DATA_JOB = {
  id: 'data-job-fixture',
  workspace_id: WORKSPACE_ID,
  kind: 'export',
  entity_type: 'issues',
  format: 'csv',
  status: 'completed',
  total_rows: 42,
  succeeded_rows: 42,
  failed_rows: 0,
  source_attachment_id: null,
  result_attachment_id: null,
  failure_reason: null,
  requested_by: MEMBER_ID,
  mapping: { columns: [] },
  params: { scope: 'workspace', locale: 'zh-CN' },
  started_at: FIXTURE_TIME,
  finished_at: '2026-07-25T08:00:05.000Z',
  created_at: FIXTURE_TIME,
  updated_at: '2026-07-25T08:00:05.000Z',
};

const API_TOKEN = {
  id: 'token-fixture',
  name: 'Release automation',
  prefix: 'mesh_pat_fixture',
  scopes: ['issue:read', 'comment:write'],
  role_override: null,
  owner_member_id: MEMBER_ID,
  expires_at: null,
  last_used_at: FIXTURE_TIME,
  revoked_at: null,
  created_at: FIXTURE_TIME,
};

const AUDIT_ENTRY = {
  id: 'audit-fixture',
  actor_member_id: MEMBER_ID,
  actor_kind: 'member',
  action: 'workspace.settings.viewed',
  resource_type: 'workspace',
  resource_id: WORKSPACE_ID,
  ip_address: '127.0.0.1',
  metadata: { source: 'settings' },
  created_at: FIXTURE_TIME,
};

/**
 * Respond to a settings/public-flow fixture route.
 *
 * @returns {boolean} true when the response has been completed, otherwise false.
 */
export function handleExtendedSettingsRoute(req, res, url) {
  const method = req.method ?? 'GET';
  const path = url.pathname;

  // Successful OAuth exchange intentionally navigates the SPA to `/`.
  if (/^\/api\/v1\/auth\/oauth\/[a-z0-9_-]+\/callback$/i.test(path) && method === 'GET') {
    sendJson(
      res,
      200,
      single({
        access_token: 'fixture-oauth-access-token',
        token_type: 'bearer',
        expires_in: 3600,
      }),
    );
    return true;
  }

  if (path === '/api/v1/invitations/preview' && method === 'GET') {
    sendJson(
      res,
      200,
      single({
        valid: true,
        workspace_name: 'Acme',
        workspace_logo_url: null,
        role: 'member',
        expires_at: '2026-08-25T08:00:00.000Z',
        appearance: { default_theme: 'light' },
      }),
    );
    return true;
  }

  // SecuritySettingsSection reads the same principal route as shell preference bootstrap.
  if (path === '/api/v1/me' && method === 'GET') {
    sendJson(res, 200, single(CURRENT_USER));
    return true;
  }

  if (path === '/api/v1/users/me' && method === 'GET') {
    sendJson(
      res,
      200,
      single({
        user: {
          id: CURRENT_USER.id,
          email: CURRENT_USER.email,
          display_name: CURRENT_USER.display_name,
        },
        memberships: [
          {
            workspace_id: WORKSPACE_ID,
            workspace_name: 'Acme',
            workspace_slug: 'acme',
            role: 'owner',
            status: 'active',
            joined_at: FIXTURE_TIME,
          },
        ],
      }),
    );
    return true;
  }

  if (path === '/api/v1/notification-preferences' && method === 'GET') {
    sendJson(res, 200, list(NOTIFICATION_PREFERENCES));
    return true;
  }

  if (path === '/api/v1/sessions' && method === 'GET') {
    sendJson(
      res,
      200,
      list([
        {
          id: 'session-fixture',
          type: 'web',
          user_agent: 'Fixture Browser',
          ip_address: '127.0.0.1',
          created_at: FIXTURE_TIME,
          last_active_at: FIXTURE_TIME,
          expires_at: '2026-08-25T08:00:00.000Z',
          current: true,
        },
      ]),
    );
    return true;
  }

  if (path === '/api/v1/auth/oauth/identities' && method === 'GET') {
    sendJson(
      res,
      200,
      list([
        {
          provider: 'github',
          provider_email: 'ana@mesh.dev',
          created_at: FIXTURE_TIME,
        },
        {
          provider: 'google',
          provider_email: 'ana@example.com',
          created_at: FIXTURE_TIME,
        },
      ]),
    );
    return true;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/invitations` && method === 'GET') {
    sendJson(res, 200, list([INVITATION]));
    return true;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/members` && method === 'GET') {
    sendJson(res, 200, list(SETTINGS_MEMBERS));
    return true;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/labels` && method === 'GET') {
    sendJson(res, 200, list([LABEL]));
    return true;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/custom-fields` && method === 'GET') {
    sendJson(res, 200, list([CUSTOM_FIELD]));
    return true;
  }

  if (path === '/api/v1/data-jobs' && method === 'GET') {
    sendJson(res, 200, list([DATA_JOB]));
    return true;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/api-tokens` && method === 'GET') {
    sendJson(res, 200, list([API_TOKEN]));
    return true;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/audit-logs` && method === 'GET') {
    sendJson(res, 200, list([AUDIT_ENTRY]));
    return true;
  }

  return false;
}
