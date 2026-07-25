/**
 * Mesh 前端契约 mock 服务端(e2e 与骨架演示区共用)。
 *
 * 实现 docs/specs/README.md 的权威契约:
 * - §6.14 三类成功包络 / 游标分页(keyset)/ 乐观并发(If-Match→409 conflict)/
 *         Idempotency-Key 去重(§6.5)/ 统一错误信封与具名 code / 过滤限制错误码
 * - §6.7  WebSocket 实时契约:子协议鉴权(Sec-WebSocket-Protocol,token 绝不进 URL query,§6.16)、
 *         频道内单调 seq、subscribe/resume_from 重放、resync_required + REST 对账水位、ping/pong
 * - §6.18 i18n 目录端点(ETag/304 版本缓存语义)
 *
 * 仅用于前端自测;不是后端实现(后端归阶段 1·A)。
 */
import { createServer } from 'node:http';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.MESH_MOCK_PORT ?? 8901);
export const AUTH_SUBPROTOCOL = 'mesh.auth.v1';

// ---------------------------------------------------------------------------
// 内存数据(单进程测试用,可随时 reset)
// ---------------------------------------------------------------------------

const DAY = 24 * 60 * 60 * 1000;
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

const state = {
  issues: seedIssues(),
  idempotency: new Map(), // key → { status, body }
  eventLog: new Map(), // topic → [{ seq, type, topic, ts, data }]
  seqs: new Map(), // topic → last seq
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

// ---------------------------------------------------------------------------
// 实时事件广播
// ---------------------------------------------------------------------------

const wsClients = new Set();

function emitEvent(topic, type, data) {
  const last = state.seqs.get(topic) ?? 0;
  const seq = last + 1;
  state.seqs.set(topic, seq);
  const frame = { seq, type, topic, ts: new Date().toISOString(), data };
  const log = state.eventLog.get(topic) ?? [];
  log.push(frame);
  state.eventLog.set(topic, log);
  for (const client of wsClients) {
    if (client.mesh?.topics?.has(topic) && client.readyState === 1) {
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

  if (path === '/api/v1/demo/reset' && req.method === 'POST') {
    resetState();
    sendJson(res, 200, envelope({ reset: true }));
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
      emitEvent('workspace:ws-1:issues', 'issue.updated', updated);
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
    emitEvent('workspace:ws-1:issues', 'issue.created', issue);
    const responseBody = envelope(issue);
    if (typeof idemKey === 'string') {
      state.idempotency.set(idemKey, { status: 201, body: responseBody });
    }
    sendJson(res, 201, responseBody);
    return;
  }

  // ---- 事件注入(e2e 与演示区触发实时帧)---------------------------------
  if (path === '/api/v1/demo/emit' && req.method === 'POST') {
    const body = (await readBody(req)) ?? {};
    const { topic, type, data } = body;
    if (typeof topic !== 'string' || typeof type !== 'string') {
      sendJson(res, 400, errorEnvelope('validation_error', 'topic and type are required'));
      return;
    }
    const frame = emitEvent(topic, type, data ?? {});
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

  sendJson(res, 404, errorEnvelope('not_found', `no mock route for ${req.method} ${path}`));
}

// ---------------------------------------------------------------------------
// 启动
// ---------------------------------------------------------------------------

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
  handleRequest(req, res, url).catch((err) => {
    sendJson(res, 500, errorEnvelope('internal_error', 'mock server error'));
    console.error('[mock-server]', err);
  });
});

// §6.16 硬约束:token 只经子协议传递,绝不进 URL query。
// handleProtocols 收到客户端提供的子协议集合 ['mesh.auth.v1', <token>];
// 缺少鉴权子协议 → 拒绝握手(false)。
const wss = new WebSocketServer({
  server,
  path: '/ws',
  handleProtocols(protocols) {
    if (!protocols.has(AUTH_SUBPROTOCOL)) return false;
    return AUTH_SUBPROTOCOL;
  },
  verifyClient(info) {
    const offered = String(info.req.headers['sec-websocket-protocol'] ?? '');
    return offered.split(',').map((s) => s.trim()).includes(AUTH_SUBPROTOCOL);
  },
});

wss.on('connection', (socket, req) => {
  const offered = String(req.headers['sec-websocket-protocol'] ?? '')
    .split(',')
    .map((s) => s.trim());
  const token = offered.find((p) => p !== AUTH_SUBPROTOCOL);
  socket.mesh = { topics: new Set(), token };
  wsClients.add(socket);

  socket.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString('utf8'));
    } catch {
      return;
    }
    if (msg.op === 'ping') {
      socket.send(JSON.stringify({ op: 'pong' }));
      return;
    }
    if (msg.op === 'subscribe' && typeof msg.topic === 'string') {
      socket.mesh.topics.add(msg.topic);
      socket.send(JSON.stringify({ op: 'subscribed', topic: msg.topic }));
      const log = state.eventLog.get(msg.topic) ?? [];
      const channelMax = state.seqs.get(msg.topic) ?? 0;
      // 游标过旧(早于保留窗口)→ resync_required + 对账水位(§6.7)
      // mock 语义:保留窗口 = 最近 100 条;resume_from 早于窗口起点即过旧。
      const retentionStart = Math.max(1, channelMax - 99);
      if (typeof msg.resume_from === 'number' && msg.resume_from < retentionStart) {
        socket.send(
          JSON.stringify({
            op: 'resync_required',
            topic: msg.topic,
            watermark: channelMax,
            rest: `/api/v1/demo/issues?since=`,
          }),
        );
        return;
      }
      // 顺序补发缺口(重放真源,§6.7)
      for (const frame of log) {
        if (typeof msg.resume_from !== 'number' || frame.seq >= msg.resume_from) {
          socket.send(JSON.stringify(frame));
        }
      }
      return;
    }
    if (msg.op === 'unsubscribe' && typeof msg.topic === 'string') {
      socket.mesh.topics.delete(msg.topic);
    }
  });

  socket.on('close', () => wsClients.delete(socket));
  socket.on('error', () => wsClients.delete(socket));
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[mock-server] listening on http://127.0.0.1:${PORT} (ws: /ws)`);
});
