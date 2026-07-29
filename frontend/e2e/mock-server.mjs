/**
 * Mesh 前端契约 mock 服务端(e2e 与骨架演示区共用)。
 *
 * 实现 docs/specs/README.md 的权威契约:
 * - §6.14 三类成功包络 / 游标分页(keyset)/ 乐观并发(If-Match→409 conflict)/
 *         Idempotency-Key 去重(§6.5)/ 统一错误信封与具名 code / 过滤限制错误码
 * - §6.7  WebSocket 实时契约 —— **与后端 v0.1.0(`backend/src/mesh/realtime/session.py`)
 *         逐帧对齐的忠实镜像**:首帧鉴权 {op:'auth',token} → {op:'auth_ok'}(token 绝不进
 *         URL query,§6.16)、频道内单调 seq、{op:'event',channel,seq,event,payload}、
 *         {op:'subscribed',channel,last_seq}、resume_from 重放、resync_required +
 *         对账 REST(/api/v1/realtime/events?channel=&since=)、ping/pong
 * - §6.18 i18n 目录端点(ETag/304 版本缓存语义)
 *
 * 仅用于前端自测;不是后端实现(后端归阶段 1·A,已发版 v0.1.0)。
 */
import { createServer } from 'node:http';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.MESH_MOCK_PORT ?? 8901);
const AUTH_TIMEOUT_MS = 10_000;
const EVENTS_PAGE_SIZE = 50;
const DEV_TOKEN_PREFIX = 'mesh-dev:';

// ---------------------------------------------------------------------------
// 内存数据(单进程测试用,可随时 reset)
// ---------------------------------------------------------------------------

const BASE_TIME = Date.UTC(2026, 6, 25, 8, 0, 0); // 2026-07-25T08:00:00Z

function isoAt(offsetMs) {
  return new Date(BASE_TIME + offsetMs).toISOString();
}

function seedIssues() {
  const categories = ['todo', 'in_progress', 'in_review', 'done'];
  return Array.from({ length: 8 }, (_, i) => ({
    id: `issue-${i + 1}`,
    identifier: `MESH-${i + 1}`,
    title: `骨架演示工作项 ${i + 1}`,
    status_category: categories[i % categories.length],
    assignee_id: i % 2 === 0 ? 'member-human-1' : 'member-agent-1',
    updated_at: isoAt(i * 60_000),
    visibility: { workspace_id: 'ws-1', project_id: i % 3 === 0 ? null : 'project-1' },
  }));
}

// ---------------------------------------------------------------------------
// 全局搜索 fixture(search-command-palette.md §3.2 结果形状:结构化 context +
// 消息目录徽章 + codepoint 高亮区间;url 为 §3.4 规范深链)
// ---------------------------------------------------------------------------

const SEARCH_ENTITY_TYPES = new Set(['issue', 'member', 'agent', 'project', 'view', 'chat_session']);

const SEARCH_FIXTURES = [
  {
    type: 'issue',
    id: 'sr-issue-1',
    title: 'Login page crashes on Safari',
    context: {
      identifier: 'WEB-124',
      project: { id: 'p-1', name: 'Website' },
      status: { id: 's-3', name: 'In Progress', category: 'in_progress' },
    },
    icon: 'issue',
    url: '/w/acme/issues/by-identifier/WEB-124',
    badge: { kind: 'status', label_key: 'issue.status.name', label_params: { name: 'In Progress' }, color: 'info' },
  },
  {
    type: 'issue',
    id: 'sr-issue-2',
    title: 'Login rate limiting',
    context: {
      identifier: 'WEB-130',
      project: null,
      status: { id: 's-1', name: 'Todo', category: 'todo' },
    },
    icon: 'issue',
    url: '/w/acme/issues/by-identifier/WEB-130',
    badge: { kind: 'status', label_key: 'issue.status.name', label_params: { name: 'Todo' }, color: 'status' },
  },
  {
    type: 'member',
    id: 'sr-member-1',
    title: 'Zhang Wei',
    context: { member_type: 'human', role: 'admin' },
    icon: 'member',
    url: '/w/acme/members/sr-member-1',
    badge: { kind: 'member_type', label_key: 'member.type.human', label_params: {}, color: 'info' },
  },
  {
    type: 'agent',
    id: 'sr-agent-1',
    title: 'Code Assistant',
    context: {
      member_type: 'agent',
      role: 'member',
      capacity: { running: 2, queued: 1, awaiting_approval: 0 },
    },
    icon: 'agent',
    url: '/w/acme/members/sr-agent-1',
    badge: { kind: 'member_type', label_key: 'member.type.agent', label_params: {}, color: 'info' },
  },
  {
    type: 'project',
    id: 'sr-project-1',
    title: 'Website Revamp',
    context: { visibility: 'public', key: 'WEB' },
    icon: 'project',
    url: '/w/acme/projects/sr-project-1',
    badge: { kind: 'visibility', label_key: 'project.visibility.public', label_params: {}, color: 'success' },
  },
  {
    type: 'view',
    id: 'sr-view-1',
    title: 'Active Website Tasks',
    context: { scope: 'workspace' },
    icon: 'view',
    url: '/w/acme/views/sr-view-1',
  },
  {
    type: 'chat_session',
    id: 'sr-chat-1',
    title: 'Release planning chat',
    context: { participants_count: 3, agent: { id: 'sr-agent-1', name: 'Code Assistant' } },
    icon: 'chat_session',
    url: '/w/acme/chat/sr-chat-1',
  },
];

const FAVORITES_FIXTURES = [
  {
    id: 'fav-1',
    workspace_id: 'ws-1',
    member_id: 'member-human-1',
    target_type: 'issue',
    target_id: 'sr-issue-1',
    created_at: isoAt(2 * 60_000),
  },
  {
    id: 'fav-2',
    workspace_id: 'ws-1',
    member_id: 'member-human-1',
    target_type: 'project',
    target_id: 'sr-project-1',
    created_at: isoAt(1 * 60_000),
  },
];

const state = {
  issues: seedIssues(),
  idempotency: new Map(), // key → { status, body }
  eventLog: new Map(), // channel → [{ op:'event', channel, seq, event, payload }]
  seqs: new Map(), // channel → last seq
};

function resetState() {
  state.issues = seedIssues();
  state.idempotency.clear();
  state.eventLog.clear();
  state.seqs.clear();
}

// ---------------------------------------------------------------------------
// HTTP 辅助
// ---------------------------------------------------------------------------

function sendJson(res, status, body, headers = {}) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers':
      'Authorization, Content-Type, If-Match, Idempotency-Key, If-None-Match',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    'Access-Control-Expose-Headers': 'ETag, Retry-After',
    ...headers,
  });
  res.end(payload);
}

function sendEmpty(res, status, headers = {}) {
  res.writeHead(status, {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers':
      'Authorization, Content-Type, If-Match, Idempotency-Key, If-None-Match',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    ...headers,
  });
  res.end();
}

function envelope(data) {
  return { data };
}

function errorEnvelope(code, message, details) {
  return { error: { code, message, ...(details ? { details } : {}) } };
}

/** auth §3.1 会话凭证:access 带 mesh-dev: 前缀(与鉴权端点/WS 首帧一致) */
function sessionTokens(refreshToken) {
  return {
    access_token: DEV_TOKEN_PREFIX + 'ws-1',
    token_type: 'Bearer',
    expires_in: 900,
    refresh_token: refreshToken,
  };
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) {
        resolve(undefined);
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(err);
      }
    });
    req.on('error', reject);
  });
}

function encodeCursor(offset) {
  return Buffer.from(`offset:${offset}`, 'utf8').toString('base64url');
}

function decodeCursor(cursor) {
  try {
    const raw = Buffer.from(cursor, 'base64url').toString('utf8');
    const match = /^offset:(\d+)$/.exec(raw);
    return match ? Number(match[1]) : 0;
  } catch {
    return 0;
  }
}

/** 与后端同形的 dev 鉴权:mesh-dev:<workspace-uuid> */
function isAuthorized(req) {
  const auth = req.headers['authorization'] ?? '';
  return auth.startsWith('Bearer ' + DEV_TOKEN_PREFIX);
}

// ---------------------------------------------------------------------------
// 实时事件广播(§6.7,帧形态对齐后端 v0.1.0)
// ---------------------------------------------------------------------------

const wsClients = new Set();

function emitEvent(channel, event, payload) {
  const last = state.seqs.get(channel) ?? 0;
  const seq = last + 1;
  state.seqs.set(channel, seq);
  const frame = { op: 'event', channel, seq, event, payload };
  const log = state.eventLog.get(channel) ?? [];
  log.push(frame);
  state.eventLog.set(channel, log);
  for (const client of wsClients) {
    if (client.mesh?.authenticated && client.mesh?.channels?.has(channel) && client.readyState === 1) {
      client.send(JSON.stringify(frame));
    }
  }
  return frame;
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------

const PAGE_SIZE = 5;

async function handleRequest(req, res, url) {
  const path = url.pathname;

  if (req.method === 'OPTIONS') {
    sendEmpty(res, 204);
    return;
  }

  if (path === '/healthz') {
    sendJson(res, 200, { ok: true });
    return;
  }

  // ---- auth REST(auth.md §3.1 / §4.1 e2e 冒烟:真实账号登录 / MFA / OAuth 往返)----
  // 会话凭证统一签 mesh-dev: 前缀,与其余鉴权端点/WS 首帧鉴权一致。
  if (path === '/api/v1/auth/login' && req.method === 'POST') {
    const body = await readBody(req);
    const email = String(body?.email ?? '');
    if (email === 'mfa@corp.com') {
      sendJson(res, 200, envelope({ mfa_required: true, mfa_ticket: 'mfa-ticket-1' }));
      return;
    }
    if (email === 'locked@corp.com') {
      sendJson(res, 423, errorEnvelope('account_locked', 'too many failed attempts'));
      return;
    }
    sendJson(res, 200, envelope(sessionTokens('rt-login')));
    return;
  }

  if (path === '/api/v1/auth/register' && req.method === 'POST') {
    const body = await readBody(req);
    sendJson(
      res,
      201,
      envelope({
        id: 'u-e2e',
        email: String(body?.email ?? 'new@corp.com'),
        email_verified: false,
        display_name: String(body?.display_name ?? 'New User'),
        avatar_url: null,
        status: 'active',
        timezone: 'UTC',
        settings: {},
        mfa_enabled: false,
        last_login_at: null,
        created_at: new Date().toISOString(),
      }),
    );
    return;
  }

  if (path === '/api/v1/auth/mfa/verify' && req.method === 'POST') {
    const body = await readBody(req);
    if (body?.code === '123456') {
      sendJson(res, 200, envelope(sessionTokens('rt-mfa')));
      return;
    }
    sendJson(res, 422, errorEnvelope('invalid_credentials', 'invalid MFA code'));
    return;
  }

  // OAuth 登录往返(§4.5 step 5;mock 提供商即刻"授权"回跳前端回调路由)。
  const oauthStartMatch = /^\/api\/v1\/auth\/oauth\/([^/]+)\/start$/.exec(path);
  if (oauthStartMatch !== null && req.method === 'GET') {
    const redirectUri = url.searchParams.get('redirect_uri');
    if (!redirectUri) {
      sendJson(
        res,
        400,
        errorEnvelope('validation_error', 'redirect_uri is required', { field: 'redirect_uri' }),
      );
      return;
    }
    const sep = redirectUri.includes('?') ? '&' : '?';
    res.writeHead(302, {
      Location: `${redirectUri}${sep}code=mockcode&state=mockstate`,
      'Access-Control-Allow-Origin': '*',
    });
    res.end();
    return;
  }

  const oauthCallbackMatch = /^\/api\/v1\/auth\/oauth\/([^/]+)\/callback$/.exec(path);
  if (oauthCallbackMatch !== null && req.method === 'GET') {
    const code = url.searchParams.get('code');
    const callbackState = url.searchParams.get('state');
    if (!code || callbackState !== 'mockstate') {
      sendJson(res, 400, errorEnvelope('invalid_oauth_state', 'invalid or expired OAuth state'));
      return;
    }
    sendJson(res, 200, envelope(sessionTokens('rt-oauth')));
    return;
  }

  if (path === '/api/v1/demo/reset' && req.method === 'POST') {
    resetState();
    sendJson(res, 200, envelope({ reset: true }));
    return;
  }

  // ---- 实时对账端点(§6.7,与后端 /api/v1/realtime/events 同形)-----------
  // seq > since,keyset 分页;Bearer 鉴权(与真实后端一致)。
  if (path === '/api/v1/realtime/events' && req.method === 'GET') {
    if (!isAuthorized(req)) {
      sendJson(res, 401, errorEnvelope('unauthorized', 'missing bearer token'));
      return;
    }
    const channel = url.searchParams.get('channel') ?? '';
    const since = Number(url.searchParams.get('since') ?? '0') || 0;
    const log = (state.eventLog.get(channel) ?? []).filter((frame) => frame.seq > since);
    const cursor = url.searchParams.get('cursor');
    const offset = cursor ? decodeCursor(cursor) : 0;
    const page = log.slice(offset, offset + EVENTS_PAGE_SIZE);
    const nextOffset = offset + EVENTS_PAGE_SIZE;
    const nextCursor = nextOffset < log.length ? encodeCursor(nextOffset) : null;
    const data = page.map((frame) => ({
      channel: frame.channel,
      seq: frame.seq,
      event: frame.event,
      payload: frame.payload,
    }));
    sendJson(res, 200, { data, next_cursor: nextCursor });
    return;
  }

  // ---- 列表包络:keyset 游标分页(§6.14)---------------------------------
  if (path === '/api/v1/demo/issues' && req.method === 'GET') {
    const cursor = url.searchParams.get('cursor');
    const since = url.searchParams.get('since');
    let items = state.issues;
    if (since) {
      items = items.filter((i) => i.updated_at > since);
      sendJson(res, 200, envelope(items)); // since 增量拉取(轮询降级,kanban §3.5)
      return;
    }
    const offset = cursor ? decodeCursor(cursor) : 0;
    const page = items.slice(offset, offset + PAGE_SIZE);
    const nextOffset = offset + PAGE_SIZE;
    const nextCursor = nextOffset < items.length ? encodeCursor(nextOffset) : null;
    sendJson(res, 200, { data: page, next_cursor: nextCursor });
    return;
  }

  // ---- 分组「整体游标」包络(§6.14 / kanban §3.4)-------------------------
  if (path === '/api/v1/demo/board' && req.method === 'GET') {
    const grouped = ['todo', 'in_progress', 'in_review', 'done'].map((key, idx) => {
      const inGroup = state.issues.filter((i) => i.status_category === key);
      return {
        key,
        label: key,
        count: inGroup.length,
        ...(idx === 1 ? { wip: 3 } : {}),
        data: inGroup,
      };
    });
    sendJson(res, 200, { groups: grouped, next_cursor: null });
    return;
  }

  // ---- 单对象包络 + 乐观并发(§6.14:If-Match / 409 conflict)------------
  const issueMatch = /^\/api\/v1\/demo\/issues\/([\w-]+)$/.exec(path);
  if (issueMatch) {
    const id = issueMatch[1];
    const issue = state.issues.find((i) => i.id === id);

    if (req.method === 'GET') {
      if (!issue) {
        sendJson(res, 404, errorEnvelope('not_found', `issue ${id} not found`));
        return;
      }
      sendJson(res, 200, envelope(issue));
      return;
    }

    if (req.method === 'PATCH') {
      if (!issue) {
        sendJson(res, 404, errorEnvelope('not_found', `issue ${id} not found`));
        return;
      }
      const ifMatch = req.headers['if-match'];
      if (ifMatch && ifMatch !== issue.updated_at) {
        sendJson(
          res,
          409,
          errorEnvelope('conflict', 'version mismatch', {
            current_version: issue.updated_at,
          }),
        );
        return;
      }
      const body = (await readBody(req)) ?? {};
      const updated = {
        ...issue,
        ...body,
        id: issue.id,
        updated_at: new Date().toISOString(),
      };
      state.issues = state.issues.map((i) => (i.id === id ? updated : i));
      // 变更经实时频道广播(演示增量合并)
      const channel = url.searchParams.get('channel') ?? defaultChannelForIssue();
      emitEvent(channel, 'issue.updated', {
        id: updated.id,
        identifier: updated.identifier,
        title: updated.title,
        status_category: updated.status_category,
        updated_at: updated.updated_at,
      });
      sendJson(res, 200, envelope(updated));
      return;
    }
  }

  // ---- 创建:Idempotency-Key 去重(§6.5/§6.14)----------------------------
  if (path === '/api/v1/demo/issues' && req.method === 'POST') {
    const idemKey = req.headers['idempotency-key'];
    if (typeof idemKey === 'string' && state.idempotency.has(idemKey)) {
      const first = state.idempotency.get(idemKey);
      sendJson(res, first.status, first.body); // 重复键返回首次结果
      return;
    }
    const body = (await readBody(req)) ?? {};
    if (typeof body.title !== 'string' || body.title.length === 0) {
      sendJson(res, 400, errorEnvelope('validation_error', 'title is required'));
      return;
    }
    const issue = {
      id: `issue-${state.issues.length + 1}`,
      identifier: `MESH-${state.issues.length + 1}`,
      title: body.title,
      status_category: 'todo',
      assignee_id: null,
      updated_at: new Date().toISOString(),
      visibility: { workspace_id: 'ws-1', project_id: null },
    };
    state.issues = [...state.issues, issue];
    emitEvent(defaultChannelForIssue(), 'issue.created', {
      id: issue.id,
      identifier: issue.identifier,
      title: issue.title,
      status_category: issue.status_category,
      updated_at: issue.updated_at,
    });
    const responseBody = envelope(issue);
    if (typeof idemKey === 'string') {
      state.idempotency.set(idemKey, { status: 201, body: responseBody });
    }
    sendJson(res, 201, responseBody);
    return;
  }

  // ---- 保留窗口清理(模拟后端 retention purge;后端 e2e 以 SQL DELETE 达成)---
  if (path === '/api/v1/demo/purge' && req.method === 'POST') {
    const body = (await readBody(req)) ?? {};
    const { channel, before_seq } = body;
    if (typeof channel !== 'string' || typeof before_seq !== 'number') {
      sendJson(res, 400, errorEnvelope('validation_error', 'channel and before_seq are required'));
      return;
    }
    const log = state.eventLog.get(channel) ?? [];
    const kept = log.filter((frame) => frame.seq >= before_seq);
    state.eventLog.set(channel, kept);
    sendJson(res, 200, envelope({ kept: kept.length }));
    return;
  }

  // ---- 事件注入(e2e 触发实时帧;经唯一写入路径广播)----------------------
  if (path === '/api/v1/demo/emit' && req.method === 'POST') {
    const body = (await readBody(req)) ?? {};
    const { channel, event, payload } = body;
    if (typeof channel !== 'string' || typeof event !== 'string') {
      sendJson(res, 400, errorEnvelope('validation_error', 'channel and event are required'));
      return;
    }
    const frame = emitEvent(channel, event, payload ?? {});
    sendJson(res, 201, envelope(frame));
    return;
  }

  // ---- 过滤限制错误码(§6.14)--------------------------------------------
  if (path === '/api/v1/demo/filter-limit' && req.method === 'GET') {
    const kind = url.searchParams.get('kind');
    if (kind === 'complex') {
      sendJson(
        res,
        400,
        errorEnvelope('filter_too_complex', 'filters exceed depth 3 / 20 conditions', {
          max_depth: 3,
          max_conditions: 20,
        }),
      );
      return;
    }
    sendJson(
      res,
      422,
      errorEnvelope('query_cost_exceeded', 'estimated query cost too high; narrow conditions'),
    );
    return;
  }

  // ---- 统一错误信封样本(§6.14)-------------------------------------------
  const errorMatch = /^\/api\/v1\/demo\/errors\/([\w_]+)$/.exec(path);
  if (errorMatch && req.method === 'GET') {
    const code = errorMatch[1];
    const table = {
      unauthorized: [401, 'credentials missing or invalid'],
      forbidden: [403, 'no permission'],
      not_found: [404, 'resource not found'],
      conflict: [409, 'version conflict'],
      gone: [410, 'resource gone'],
      locked: [423, 'resource locked'],
      payload_too_large: [413, 'payload too large'],
      unsupported_media_type: [415, 'unsupported media type'],
      rate_limited: [429, 'rate limit exceeded'],
      internal_error: [500, 'internal error'],
      storage_error: [502, 'storage error'],
    };
    const entry = table[code];
    if (!entry) {
      sendJson(res, 404, errorEnvelope('not_found', `unknown error sample ${code}`));
      return;
    }
    const headers = code === 'rate_limited' ? { 'Retry-After': '2' } : {};
    sendJson(res, entry[0], errorEnvelope(code, entry[1]), headers);
    return;
  }

  // ---- i18n 目录(§6.18 / i18n.md §3.1:ETag 版本缓存)--------------------
  if (path === '/api/v1/i18n/catalog' && req.method === 'GET') {
    const locale = url.searchParams.get('locale') ?? 'en';
    const catalogs = {
      en: { locale: 'en', version: 'mock00en', messages: { 'mock.hello': 'Hello' } },
      'zh-CN': { locale: 'zh-CN', version: 'mock00zh', messages: { 'mock.hello': '你好' } },
    };
    const catalog = catalogs[locale];
    if (!catalog) {
      sendJson(res, 404, errorEnvelope('not_found', `unsupported locale ${locale}`));
      return;
    }
    const ifNoneMatch = req.headers['if-none-match'];
    if (ifNoneMatch && ifNoneMatch.replace(/"/g, '') === catalog.version) {
      sendEmpty(res, 304, { ETag: `"${catalog.version}"` });
      return;
    }
    sendJson(res, 200, envelope(catalog), { ETag: `"${catalog.version}"` });
    return;
  }

  if (path === '/api/v1/i18n/missing' && req.method === 'POST') {
    sendEmpty(res, 204);
    return;
  }

  // ---- 全局搜索(search-command-palette.md §3.1/§3.2:workspace scope = 路径;
  //      空 q → 空集;types 白名单校验;limit ≤50;前缀命中给 codepoint 高亮区间)---
  const searchMatch = /^\/api\/v1\/workspaces\/([^/]+)\/search$/.exec(path);
  if (searchMatch !== null && req.method === 'GET') {
    if (!isAuthorized(req)) {
      sendJson(res, 401, errorEnvelope('unauthorized', 'missing bearer token'));
      return;
    }
    const q = (url.searchParams.get('q') ?? '').trim();
    if (q === '') {
      sendJson(res, 200, { data: [], next_cursor: null });
      return;
    }
    if ([...q].length > 120) {
      sendJson(res, 400, errorEnvelope('validation_error', 'q exceeds 120 characters'));
      return;
    }
    let typesFilter = null;
    const typesParam = url.searchParams.get('types');
    if (typesParam !== null) {
      typesFilter = typesParam.split(',').filter((item) => item !== '');
      if (typesFilter.length === 0 || typesFilter.some((item) => !SEARCH_ENTITY_TYPES.has(item))) {
        sendJson(res, 400, errorEnvelope('validation_error', 'invalid types value'));
        return;
      }
    }
    const limitParam = url.searchParams.get('limit');
    const limit = limitParam === null ? 20 : Number(limitParam);
    if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
      sendJson(res, 400, errorEnvelope('validation_error', 'limit must be 1..50'));
      return;
    }
    const lower = q.toLowerCase();
    const qLength = [...q].length;
    const matched = SEARCH_FIXTURES.filter((item) => item.title.toLowerCase().includes(lower))
      .filter((item) => typesFilter === null || typesFilter.includes(item.type))
      .slice(0, limit)
      .map((item) => {
        const index = item.title.toLowerCase().indexOf(lower);
        // 前缀命中:标注原始 title 上的 codepoint 区间 [0, len(q))(§3.2)
        return index === 0
          ? { ...item, highlight: { title: { unit: 'codepoint', ranges: [[0, qLength]] } } }
          : item;
      });
    sendJson(res, 200, { data: matched, next_cursor: null });
    return;
  }

  // ---- 收藏(§6.19:面板空态唯一服务端数据源;created_at 倒序)----------------
  if (path === '/api/v1/favorites' && req.method === 'GET') {
    if (!isAuthorized(req)) {
      sendJson(res, 401, errorEnvelope('unauthorized', 'missing bearer token'));
      return;
    }
    const favorites = [...FAVORITES_FIXTURES].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    sendJson(res, 200, { data: favorites, next_cursor: null });
    return;
  }

  sendJson(res, 404, errorEnvelope('not_found', `no mock route for ${req.method} ${path}`));
}

/** 演示 CRUD 广播的默认频道(e2e helpers 显式指定频道时以 emit 端点为准) */
function defaultChannelForIssue() {
  return process.env.MESH_MOCK_DEMO_CHANNEL ?? 'workspace:ws-1:issues';
}

// ---------------------------------------------------------------------------
// WebSocket 网关(§6.7,协议逐帧对齐后端 v0.1.0 session.py)
// ---------------------------------------------------------------------------

const wss = new WebSocketServer({ server: undefined, noServer: true });

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
  handleRequest(req, res, url).catch((err) => {
    sendJson(res, 500, errorEnvelope('internal_error', 'mock server error'));
    console.error('[mock-server]', err);
  });
});

// §6.16:token 绝不进 URL;upgrade 不经子协议协商(与后端 websocket.accept() 一致),
// 鉴权在连接建立后的首帧 {op:'auth', token} 完成。
server.on('upgrade', (req, socket, head) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
  if (url.pathname !== '/ws') {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => {
    wss.emit('connection', ws, req);
  });
});

wss.on('connection', (socket) => {
  socket.mesh = { authenticated: false, channels: new Set(), authTimer: null };
  wsClients.add(socket);

  // 首帧鉴权超时(与后端 AUTH_TIMEOUT_SECONDS 对齐)
  socket.mesh.authTimer = setTimeout(() => {
    if (!socket.mesh.authenticated) {
      socket.send(JSON.stringify({ op: 'error', code: 'unauthorized', message: 'authentication timed out' }));
      socket.close();
    }
  }, AUTH_TIMEOUT_MS);

  socket.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString('utf8'));
    } catch {
      return;
    }

    // ---- 首帧鉴权 -------------------------------------------------------
    if (!socket.mesh.authenticated) {
      if (msg.op === 'auth' && typeof msg.token === 'string' && msg.token.startsWith(DEV_TOKEN_PREFIX)) {
        socket.mesh.authenticated = true;
        clearTimeout(socket.mesh.authTimer);
        socket.send(JSON.stringify({ op: 'auth_ok' }));
      } else {
        socket.send(JSON.stringify({ op: 'error', code: 'unauthorized', message: 'first frame must be auth' }));
        socket.close();
      }
      return;
    }

    // ---- 订阅:重放 + subscribed 确认 / resync_required --------------------
    if (msg.op === 'subscribe' && typeof msg.channel === 'string') {
      const channel = msg.channel;
      socket.mesh.channels.add(channel);
      const log = state.eventLog.get(channel) ?? [];
      const watermark = state.seqs.get(channel) ?? 0;
      const minSeq = log.length > 0 ? log[0].seq : null;
      const resumeFrom = typeof msg.resume_from === 'number' ? msg.resume_from : 0;

      // 游标过旧(早于保留窗口)→ resync_required + 对账水位(§6.7)
      if (resumeFrom > 0) {
        const stale =
          (minSeq !== null && resumeFrom < minSeq) || (minSeq === null && resumeFrom <= watermark);
        if (stale) {
          socket.mesh.channels.delete(channel);
          socket.send(
            JSON.stringify({
              op: 'resync_required',
              channel,
              watermark,
              rest: `/api/v1/realtime/events?channel=${encodeURIComponent(channel)}&since=${resumeFrom}`,
            }),
          );
          return;
        }
      }

      // 顺序补发缺口(重放真源,§6.7)
      for (const frame of log) {
        if (frame.seq >= resumeFrom) {
          socket.send(JSON.stringify(frame));
        }
      }
      socket.send(JSON.stringify({ op: 'subscribed', channel, last_seq: watermark }));
      return;
    }

    if (msg.op === 'unsubscribe' && typeof msg.channel === 'string') {
      socket.mesh.channels.delete(msg.channel);
      return;
    }

    if (msg.op === 'ping') {
      socket.send(JSON.stringify({ op: 'ping' }));
    }
  });

  const cleanup = () => {
    clearTimeout(socket.mesh.authTimer);
    wsClients.delete(socket);
  };
  socket.on('close', cleanup);
  socket.on('error', cleanup);
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[mock-server] listening on http://127.0.0.1:${PORT} (ws: /ws, first-frame auth)`);
});
