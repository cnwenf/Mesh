/**
 * api.ts 契约层测试(autopilot.md §3.1):路径拼装 + 方法 + 包络解包。
 * client 以最小桩替代(记录 method/path/body/query)。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  approveRun,
  autopilotChannel,
  cancelRun,
  createAutopilot,
  createWebhookSecret,
  deleteAutopilot,
  getAutopilot,
  getAutopilotRun,
  getKillSwitchState,
  inboundWebhookUrl,
  listAutopilotRuns,
  listAutopilots,
  listRunArtifacts,
  listWebhookSecrets,
  patchAutopilot,
  pauseAutopilot,
  previewSchedule,
  rejectRun,
  resumeAutopilot,
  rotateWebhookSecret,
  setKillSwitch,
  testRunAutopilot,
  workspaceAutopilotsChannel,
} from '../api';

interface Call {
  method: string;
  path: string;
  opts?: unknown;
}

function makeClient(): { client: MeshApiClient; calls: Call[] } {
  const calls: Call[] = [];
  const client = {
    request: vi.fn(async (method: string, path: string, opts?: unknown) => {
      calls.push({ method, path, opts });
      return { ok: true };
    }),
    list: vi.fn(async (path: string, opts?: unknown) => {
      calls.push({ method: 'LIST', path, opts });
      return { data: [{ id: 'x' }], next_cursor: 'c1' };
    }),
  } as unknown as MeshApiClient;
  return { client, calls };
}

const WS = 'ws-1';

describe('channel builders', () => {
  it('builds workspace and rule channels', () => {
    expect(workspaceAutopilotsChannel(WS)).toBe('workspace:ws-1:autopilots');
    expect(autopilotChannel('ap-1')).toBe('autopilot:ap-1');
  });

  it('builds the inbound webhook url from the api base', () => {
    const url = inboundWebhookUrl('whk_token');
    expect(url).toContain('/api/v1/webhooks/inbound/whk_token');
  });
});

describe('rule endpoints', () => {
  it('lists with filters and cursor', async () => {
    const { client, calls } = makeClient();
    const result = await listAutopilots(client, WS, {
      status: 'active',
      trigger_type: 'schedule',
      search: 'daily',
      cursor: 'cur',
      limit: 10,
    });
    expect(result.nextCursor).toBe('c1');
    expect(calls[0]).toMatchObject({
      method: 'LIST',
      path: `/api/v1/workspaces/${WS}/autopilots`,
      opts: {
        query: {
          status: 'active',
          trigger_type: 'schedule',
          search: 'daily',
          cursor: 'cur',
          limit: 10,
        },
      },
    });
  });

  it('maps crud verbs to the right paths', async () => {
    const { client, calls } = makeClient();
    await getAutopilot(client, WS, 'ap-1');
    await createAutopilot(client, WS, { name: 'r' });
    await patchAutopilot(client, WS, 'ap-1', { name: 'r2' });
    await deleteAutopilot(client, WS, 'ap-1');
    await pauseAutopilot(client, WS, 'ap-1');
    await resumeAutopilot(client, WS, 'ap-1');
    await previewSchedule(client, WS, 'ap-1', 3);
    await testRunAutopilot(client, WS, 'ap-1', { dry_run: true });
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `GET /api/v1/workspaces/${WS}/autopilots/ap-1`,
      `POST /api/v1/workspaces/${WS}/autopilots`,
      `PATCH /api/v1/workspaces/${WS}/autopilots/ap-1`,
      `DELETE /api/v1/workspaces/${WS}/autopilots/ap-1`,
      `POST /api/v1/workspaces/${WS}/autopilots/ap-1/pause`,
      `POST /api/v1/workspaces/${WS}/autopilots/ap-1/resume`,
      `GET /api/v1/workspaces/${WS}/autopilots/ap-1/preview-schedule`,
      `POST /api/v1/workspaces/${WS}/autopilots/ap-1/test-run`,
    ]);
    expect(calls[6].opts).toMatchObject({ query: { count: 3 } });
  });

  it('maps run endpoints', async () => {
    const { client, calls } = makeClient();
    await listAutopilotRuns(client, WS, 'ap-1', { status: 'failed' });
    await getAutopilotRun(client, WS, 'run-1');
    await listRunArtifacts(client, WS, 'run-1');
    await cancelRun(client, WS, 'run-1');
    await approveRun(client, WS, 'run-1', 'ok');
    await rejectRun(client, WS, 'run-1');
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `LIST /api/v1/workspaces/${WS}/autopilots/ap-1/runs`,
      `GET /api/v1/workspaces/${WS}/autopilot-runs/run-1`,
      `LIST /api/v1/workspaces/${WS}/autopilot-runs/run-1/artifacts`,
      `POST /api/v1/workspaces/${WS}/autopilot-runs/run-1/cancel`,
      `POST /api/v1/workspaces/${WS}/autopilot-runs/run-1/approve`,
      `POST /api/v1/workspaces/${WS}/autopilot-runs/run-1/reject`,
    ]);
    expect(calls[4].opts).toMatchObject({ body: { comment: 'ok' } });
    expect(calls[5].opts).toMatchObject({ body: {} });
  });

  it('maps kill switch + webhook secret endpoints', async () => {
    const { client, calls } = makeClient();
    await getKillSwitchState(client, WS);
    await setKillSwitch(client, WS, { enabled: true, reason: 'stop' });
    await listWebhookSecrets(client, WS);
    await createWebhookSecret(client, WS, 'prod');
    await rotateWebhookSecret(client, WS, 'sec-1');
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `GET /api/v1/workspaces/${WS}/autopilots/kill-switch`,
      `POST /api/v1/workspaces/${WS}/autopilots/kill-switch`,
      `LIST /api/v1/workspaces/${WS}/webhook-secrets`,
      `POST /api/v1/workspaces/${WS}/webhook-secrets`,
      `POST /api/v1/workspaces/${WS}/webhook-secrets/sec-1/rotate`,
    ]);
    expect(calls[3].opts).toMatchObject({ body: { label: 'prod' } });
  });
});
