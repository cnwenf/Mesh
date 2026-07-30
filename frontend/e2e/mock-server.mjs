/**
 * Mesh 前端契约 mock 服务端(e2e 契约套件专用)。
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
 * 仅用于前端自测;不是后端实现(真实后端见 backend/)。
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
    workspace_id: 'ws-1',
    identifier: `MESH-${i + 1}`,
    title: `Acme 工作项 ${i + 1}`,
    state_category: categories[i % categories.length],
    assignee_id: i % 2 === 0 ? 'member-human-1' : 'member-agent-1',
    updated_at: isoAt(i * 60_000),
    project_id: i % 3 === 0 ? null : 'project-1',
  }));
}

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

  // ---- 当前用户与成员身份(member.md §3.1 GET /users/me)------------------
  if (path === '/api/v1/users/me' && req.method === 'GET') {
    if (!isAuthorized(req)) {
      sendJson(res, 401, errorEnvelope('unauthorized', 'missing bearer token'));
      return;
    }
    sendJson(
      res,
      200,
      envelope({
        user: { id: 'user-1', email: 'jane@corp.com', display_name: 'Jane Doe' },
        memberships: [
          {
            workspace_id: 'ws-1',
            workspace_name: 'Acme',
            workspace_slug: 'acme',
            role: 'admin',
            status: 'default',
            joined_at: isoAt(0),
          },
        ],
      }),
    );
    return;
  }

  // ---- 测试治具控制端点(非产品 API:重置内存态 / 注入帧 / 保留窗口清理)---
  if (path === '/api/v1/mock/reset' && req.method === 'POST') {
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

  // ---- 列表包络:keyset 游标分页(§6.14;真实路径 /workspaces/{ws}/issues)---
  if (path === '/api/v1/workspaces/ws-1/issues' && req.method === 'GET') {
    if (!isAuthorized(req)) {
      sendJson(res, 401, errorEnvelope('unauthorized', 'missing bearer token'));
      return;
    }
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

  // ---- 单对象包络 + 乐观并发(§6.14:If-Match / 409 conflict;真实路径 /issues/{id})
  const issueMatch = /^\/api\/v1\/issues\/([\w-]+)$/.exec(path);
  if (issueMatch) {
    if (!isAuthorized(req)) {
      sendJson(res, 401, errorEnvelope('unauthorized', 'missing bearer token'));
      return;
    }
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
        state_category: updated.state_category,
        updated_at: updated.updated_at,
      });
      sendJson(res, 200, envelope(updated));
      return;
    }
  }

  // ---- 创建:Idempotency-Key 去重(§6.5/§6.14;真实路径 /workspaces/{ws}/issues)
  if (path === '/api/v1/workspaces/ws-1/issues' && req.method === 'POST') {
    if (!isAuthorized(req)) {
      sendJson(res, 401, errorEnvelope('unauthorized', 'missing bearer token'));
      return;
    }
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
      workspace_id: 'ws-1',
      identifier: `MESH-${state.issues.length + 1}`,
      title: body.title,
      state_category: 'todo',
      assignee_id: null,
      updated_at: new Date().toISOString(),
      project_id: null,
    };
    state.issues = [...state.issues, issue];
    emitEvent(defaultChannelForIssue(), 'issue.created', {
      issue: {
        id: issue.id,
        workspace_id: issue.workspace_id,
        identifier: issue.identifier,
        title: issue.title,
        state_category: issue.state_category,
        updated_at: issue.updated_at,
      },
    });
    const responseBody = envelope(issue);
    if (typeof idemKey === 'string') {
      state.idempotency.set(idemKey, { status: 201, body: responseBody });
    }
    sendJson(res, 201, responseBody);
    return;
  }

  // ---- 保留窗口清理(模拟后端 retention purge;后端 e2e 以 SQL DELETE 达成)---
  if (path === '/api/v1/mock/purge' && req.method === 'POST') {
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
  if (path === '/api/v1/mock/emit' && req.method === 'POST') {
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

  sendJson(res, 404, errorEnvelope('not_found', `no mock route for ${req.method} ${path}`));
}

/** 演示 CRUD 广播的默认频道(e2e helpers 显式指定频道时以 emit 端点为准) */
function defaultChannelForIssue() {
  return 'workspace:ws-1:issues';
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
