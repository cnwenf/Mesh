/**
 * 视觉回归专用 mock 服务端(theme.md §5.4 / Task 21)。
 *
 * 这是 e2e/mock-server.mjs 的独立 fork(端口 8911,勿与默认套件 8901 冲突),
 * 为「暗色视觉回归门禁」提供六核心页(看板 / issue 详情 / 成员 / 聊天 / 运行详情 /
 * 收件箱)恒定 fixture 数据 + 应用外壳引导数据 + 内置字体分发。默认套件对
 * mock-server.mjs 的既有消费不受影响(本文件不改动 mock-server.mjs)。
 *
 * 确定性约定:
 * - 所有时间戳取自固定基准 VISUAL_BASE_TIME,内容逐字节恒定;
 * - 文案为受控常量(无随机/自增),供 toHaveScreenshot 基线比对;
 * - 实时网关(/ws)完成首帧鉴权 + 订阅确认但**不主动推送事件**,页面渲染保持稳定。
 */
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, normalize } from 'node:path';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.MESH_MOCK_VISUAL_PORT ?? 8911);
const AUTH_TIMEOUT_MS = 10_000;
const DEV_TOKEN_PREFIX = 'mesh-dev:';

const HERE = dirname(fileURLToPath(import.meta.url));
const FONTS_DIR = join(HERE, 'fonts');

// 固定基准时间:2026-07-25T08:00:00Z(与冻结时钟 12:00:00Z 同日,相对时间恒定)。
const VISUAL_BASE_TIME = Date.UTC(2026, 6, 25, 8, 0, 0);
function isoAt(offsetMs) {
  return new Date(VISUAL_BASE_TIME + offsetMs).toISOString();
}

// ---------------------------------------------------------------------------
// 恒定 fixture 数据
// ---------------------------------------------------------------------------

const WORKSPACE_ID = 'ws-1';
const USER_ID = 'user-1';
const MEMBER_HUMAN_ID = 'member-human-1';

const ME = {
  user: { id: USER_ID, email: 'ana@mesh.dev', display_name: 'Ana' },
  memberships: [
    {
      workspace_id: WORKSPACE_ID,
      workspace_name: 'Acme',
      workspace_slug: 'acme',
      role: 'owner',
      status: 'active',
      joined_at: isoAt(0),
    },
  ],
};

const WORKSPACE_SUMMARY = {
  id: WORKSPACE_ID,
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  my_role: 'owner',
  created_at: isoAt(0),
};

const WORKSPACE_DETAIL = {
  id: WORKSPACE_ID,
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'zh-CN', default_theme: 'light' },
  my_role: 'owner',
  created_at: isoAt(0),
  updated_at: isoAt(0),
};

// 成员名册(外壳铃铛 / 上手清单与成员页共用基础集合;成员页专有列表见下)。
const MEMBERS = [
  {
    id: MEMBER_HUMAN_ID,
    member_type: 'human',
    role: 'owner',
    status: 'active',
    display_name: 'Ana',
    joined_at: isoAt(0),
    profile: { id: USER_ID, full_name: 'Ana', email: 'ana@mesh.dev', avatar_url: null },
  },
  {
    id: 'member-human-2',
    member_type: 'human',
    role: 'member',
    status: 'active',
    display_name: 'Bruno',
    joined_at: isoAt(60_000),
    profile: { id: 'user-2', full_name: 'Bruno', email: 'bruno@mesh.dev', avatar_url: null },
  },
  {
    id: 'member-agent-1',
    member_type: 'agent',
    role: 'member',
    status: 'active',
    display_name: 'Mesh Agent',
    joined_at: isoAt(120_000),
    profile: {
      id: 'agent-1',
      name: 'Mesh Agent',
      description: 'General-purpose teammate agent',
      avatar_url: null,
      is_active: true,
      role_tag: 'assistant',
      lifecycle_status: 'running',
    },
  },
];

// ---------------------------------------------------------------------------
// HTTP 辅助
// ---------------------------------------------------------------------------

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

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', ...corsHeaders(headers) });
  res.end(JSON.stringify(body));
}

function sendEmpty(res, status, headers = {}) {
  res.writeHead(status, corsHeaders(headers));
  res.end();
}

function envelope(data) {
  return { data };
}

function listEnvelope(data) {
  return { data, next_cursor: null };
}

function errorEnvelope(code, message) {
  return { error: { code, message } };
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

// ---------------------------------------------------------------------------
// 字体分发(内置 OFL woff2,@font-face 跨域加载需 ACAO:*)
// ---------------------------------------------------------------------------

const FONT_ALLOWLIST = new Set(
  [
    'noto-sans-sc-chinese-simplified-400-normal.woff2',
    'noto-sans-sc-chinese-simplified-500-normal.woff2',
    'noto-sans-sc-chinese-simplified-700-normal.woff2',
    'noto-sans-sc-latin-400-normal.woff2',
    'noto-sans-sc-latin-500-normal.woff2',
    'noto-sans-sc-latin-700-normal.woff2',
  ],
);

function serveFont(res, name) {
  if (!FONT_ALLOWLIST.has(name)) {
    sendJson(res, 404, errorEnvelope('not_found', `unknown font ${name}`));
    return;
  }
  const file = normalize(join(FONTS_DIR, name));
  if (!file.startsWith(FONTS_DIR) || !existsSync(file)) {
    sendJson(res, 404, errorEnvelope('not_found', `font ${name} missing`));
    return;
  }
  const buf = readFileSync(file);
  res.writeHead(200, corsHeaders({ 'Content-Type': 'font/woff2', 'Cache-Control': 'no-store' }));
  res.end(buf);
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------

// 页面数据路由表由 page-routes.mjs 注入(六页恒定 fixture),保持本文件聚焦引导层。
import { handlePageRoute } from './page-routes.mjs';

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

  // ---- 内置字体分发 -----------------------------------------------------
  const fontMatch = /^\/visual\/fonts\/([\w.-]+)$/.exec(path);
  if (fontMatch && req.method === 'GET') {
    serveFont(res, fontMatch[1]);
    return;
  }

  // ---- 外壳引导:当前用户 / 工作区(每个鉴权页 đều 触发)-----------------
  if (path === '/api/v1/users/me' && req.method === 'GET') {
    sendJson(res, 200, envelope(ME));
    return;
  }

  if (path === '/api/v1/users/me' && req.method === 'PATCH') {
    // 偏好同步(主题/语言/时区)fire-and-forget;回显即可,渲染不依赖。
    await readBody(req);
    sendJson(res, 200, envelope(ME.user));
    return;
  }

  if (path === '/api/v1/workspaces' && req.method === 'GET') {
    sendJson(res, 200, listEnvelope([WORKSPACE_SUMMARY]));
    return;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}` && req.method === 'GET') {
    sendJson(res, 200, envelope(WORKSPACE_DETAIL));
    return;
  }

  if (path === '/api/v1/workspaces/by-slug/acme' && req.method === 'GET') {
    sendJson(res, 200, envelope(WORKSPACE_DETAIL));
    return;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/members` && req.method === 'GET') {
    sendJson(res, 200, listEnvelope(MEMBERS));
    return;
  }

  if (path === '/api/v1/inbox/unread-count' && req.method === 'GET') {
    sendJson(res, 200, envelope({ count: 3 }));
    return;
  }

  if (path === '/api/v1/onboarding/state' && req.method === 'GET') {
    // data:null → 上手清单卡片隐藏,避免动态进度进入截图。
    sendJson(res, 200, envelope(null));
    return;
  }

  if (path === `/api/v1/workspaces/${WORKSPACE_ID}/issues` && req.method === 'GET') {
    const limit = url.searchParams.get('limit');
    const handled = handlePageRoute(req, res, url, { kind: 'workspace-issues', limit });
    if (handled) return;
    sendJson(res, 200, listEnvelope([]));
    return;
  }

  // ---- 实时对账端点(WS 已连接时不会命中;兜底返回空页)------------------
  if (path === '/api/v1/realtime/events' && req.method === 'GET') {
    sendJson(res, 200, { data: [], next_cursor: null });
    return;
  }

  // ---- 六核心页专有数据路由(看板 / issue 详情 / 成员 / 聊天 / 运行详情 / 收件箱)
  if (handlePageRoute(req, res, url, { kind: 'general' })) {
    return;
  }

  sendJson(res, 404, errorEnvelope('not_found', `no visual mock route for ${req.method} ${path}`));
}

// ---------------------------------------------------------------------------
// WebSocket 网关:首帧鉴权 + 订阅确认,不主动推送(保持页面稳定)
// ---------------------------------------------------------------------------

const wss = new WebSocketServer({ server: undefined, noServer: true });

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
  handleRequest(req, res, url).catch((err) => {
    sendJson(res, 500, errorEnvelope('internal_error', 'visual mock server error'));
    console.error('[visual-mock]', err);
  });
});

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
  socket.mesh = { authenticated: false, authTimer: null };
  socket.mesh.authTimer = setTimeout(() => {
    if (!socket.mesh.authenticated) {
      socket.send(JSON.stringify({ op: 'error', code: 'unauthorized', message: 'auth timeout' }));
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
    if (msg.op === 'subscribe' && typeof msg.channel === 'string') {
      // 确认订阅但不回放/推送任何事件 → 页面渲染保持静态,利于像素比对。
      socket.send(JSON.stringify({ op: 'subscribed', channel: msg.channel, last_seq: 0 }));
      return;
    }
    if (msg.op === 'ping') {
      socket.send(JSON.stringify({ op: 'ping' }));
    }
  });

  const cleanup = () => clearTimeout(socket.mesh.authTimer);
  socket.on('close', cleanup);
  socket.on('error', cleanup);
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[visual-mock] listening on http://127.0.0.1:${PORT} (ws: /ws, fonts: /visual/fonts/*)`);
});
