/* Real-browser UI walkthrough for the integrations acceptance round.
   Seeds via API, then drives pages like a human (login → integrations →
   test connection → webhook subscriptions → send test → issue VCS panel). */
import { chromium } from 'playwright';
import { execSync } from 'node:child_process';
import { readFileSync, mkdirSync } from 'node:fs';

const API = 'http://127.0.0.1:8000/api/v1';
const UI = 'http://127.0.0.1:3001';
const stamp = String(Date.now()).slice(-7);
const email = `ui-walk-${stamp}@walk.mesh`;
const password = 'StrongPass123!';
const slug = `walk${stamp}`;
const redisPw = readFileSync('../.env', 'utf8').match(/MESH_REDIS_PASSWORD=(.+)/)[1].trim();
const OUT = './ui-evidence';
mkdirSync(OUT, { recursive: true });

async function api(method, path, body, token) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = JSON.parse(text); } catch { /* non-JSON */ }
  return { status: res.status, data, text };
}

// ---- seed -------------------------------------------------------------
let r = await api('POST', '/auth/register', { email, password, display_name: 'UI Walk' });
console.log('register', r.status);
// Proactively complete dev-mailbox verification if a token was issued.
const devToken = execSync(
  `docker exec mesh-redis-1 redis-cli -a ${redisPw} --no-auth-warning GET mesh:devmail:verification:${email}`,
).toString().trim();
if (devToken && !devToken.startsWith('(nil)')) {
  const v = await api('POST', '/auth/verify-email', { email, token: devToken });
  console.log('verify', v.status, v.text.slice(0, 120));
}
r = await api('POST', '/auth/login', { email, password });
if (r.status !== 200) throw new Error(`login failed: ${r.status} ${r.text}`);
const authToken = r.data.data.access_token;

r = await api('POST', '/workspaces', { name: 'UI Walk WS', slug }, authToken);
console.log('workspace', r.status);
const wsId = r.data.data.id;

r = await api('POST', `/workspaces/${wsId}/integrations`, {
  kind: 'im_slack', name: 'slack-walk', config: { team_id: 'T_WALK' }, secret: 'xoxb-walk-secret',
}, authToken);
console.log('slack integration', r.status, r.text.slice(0, 120));

r = await api('POST', `/workspaces/${wsId}/integrations`, {
  kind: 'vcs_github', name: 'gh-walk', config: { installation_id: '77' }, secret: 'ghs_walk',
}, authToken);
const ghId = r.data.data.integration.id;
console.log('github integration', r.status);

r = await api('POST', `/workspaces/${wsId}/webhook-subscriptions`, {
  url: 'https://example.com/hook', event_types: [],
}, authToken);
console.log('subscription', r.status, r.text.slice(0, 100));

r = await api('POST', `/workspaces/${wsId}/issues`, { title: 'VCS link demo issue' }, authToken);
const issueId = r.data.data.id;
console.log('issue', r.status);

r = await api('POST', '/integrations/vcs/links', {
  integration_id: ghId,
  vcs_ref: { type: 'pull_request', id: `acme/demo${stamp}#77` },
  mesh_entity_type: 'issue',
  issue_id: issueId,
}, authToken);
console.log('vcs link', r.status, r.text.slice(0, 140));

// ---- browser ----------------------------------------------------------
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(30000);

await page.goto(`${UI}/login`);
await page.locator('input[type="email"]').fill(email);
await page.locator('input[type="password"]').fill(password);
await page.getByTestId('login-account-submit').click();
await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 }).catch(async () => {
  await page.screenshot({ path: `${OUT}/walk-00-login-stuck.png` });
  throw new Error('login did not leave /login');
});
console.log('logged in, url:', page.url());

// 1) Integrations page — 近7天事件量 column + health badges
await page.goto(`${UI}/integrations`);
await page.getByText(/近7天事件量|Events \(7d\)/).first().waitFor({ timeout: 30000 });
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}/walk-01-integrations.png`, fullPage: true });
console.log('shot 1: integrations list');

// 2) Test connection (slack-walk: bogus credential → auth_failed/unreachable badge)
await page.getByRole('button', { name: /测试连接|Test connection/ }).first().click();
await page.waitForTimeout(6000); // platform API round-trip (or network timeout path)
await page.screenshot({ path: `${OUT}/walk-02-after-test.png`, fullPage: true });
console.log('shot 2: after test connection');

// 3) Webhook subscriptions — 成功率 column + send test event
await page.goto(`${UI}/webhook-subscriptions`);
await page.getByText(/成功率|Success rate/).first().waitFor({ timeout: 30000 });
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}/walk-03-subscriptions.png`, fullPage: true });
console.log('shot 3: subscriptions list');

await page.getByRole('button', { name: /发送测试事件|Send test event/ }).first().click();
await page.waitForTimeout(2500);
await page.screenshot({ path: `${OUT}/walk-04-test-sent.png`, fullPage: true });
console.log('shot 4: test event sent');

// 4) Issue VCS panel — clickable deep link
await page.goto(`${UI}/issues/${issueId}`);
await page.waitForTimeout(2500);
const vcsLink = page.locator(`a[href^="https://github.com/acme/demo${stamp}/pull/"]`).first();
await vcsLink.waitFor({ timeout: 30000 });
console.log('vcs deep link href:', await vcsLink.getAttribute('href'));
await page.screenshot({ path: `${OUT}/walk-05-issue-vcs.png`, fullPage: true });
console.log('shot 5: issue VCS panel');

await browser.close();
console.log('WALKTHROUGH COMPLETE');
