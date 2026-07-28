/**
 * api.ts 契约层测试(integrations.md §3):路径拼装 + 方法 + 包络解包。
 * client 以最小桩替代(记录 method/path/body/query)。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  confirmExternalIdentity,
  createBinding,
  createIntegration,
  createSubscription,
  createVcsLink,
  deleteBinding,
  deleteIntegration,
  deleteSubscription,
  deleteVcsLink,
  getIntegration,
  getSubscription,
  integrationAuthorizeUrl,
  integrationChannel,
  linkExternalIdentity,
  listBindings,
  listDeliveries,
  listExternalIdentities,
  listIntegrationEvents,
  listIntegrations,
  listIssueVcsLinks,
  listSubscriptions,
  patchBinding,
  patchIntegration,
  patchSubscription,
  resolveVcsLink,
  resumeSubscription,
  retryDelivery,
  rotateIntegrationSecret,
  unlinkExternalIdentity,
  workspaceIntegrationsChannel,
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

describe('channel builders + urls', () => {
  it('builds workspace and integration channels', () => {
    expect(workspaceIntegrationsChannel(WS)).toBe('workspace:ws-1:integrations');
    expect(integrationChannel('i-1')).toBe('integration:i-1');
  });

  it('builds the oauth authorize url', () => {
    const url = integrationAuthorizeUrl(WS, 'vcs_github');
    expect(url).toContain('/api/v1/workspaces/ws-1/integrations/oauth/vcs_github/authorize');
  });
});

describe('integration endpoints', () => {
  it('lists with filters and cursor', async () => {
    const { client, calls } = makeClient();
    const result = await listIntegrations(client, WS, {
      kind: 'im_slack',
      status: 'active',
      cursor: 'cur',
      limit: 10,
    });
    expect(result.nextCursor).toBe('c1');
    expect(calls[0]).toMatchObject({
      method: 'LIST',
      path: `/api/v1/workspaces/${WS}/integrations`,
      opts: { query: { kind: 'im_slack', status: 'active', cursor: 'cur', limit: 10 } },
    });
  });

  it('maps integration crud verbs', async () => {
    const { client, calls } = makeClient();
    await createIntegration(client, WS, { kind: 'im_slack', name: 'n' });
    await getIntegration(client, WS, 'i-1');
    await patchIntegration(client, WS, 'i-1', { status: 'disabled' });
    await deleteIntegration(client, WS, 'i-1');
    await rotateIntegrationSecret(client, WS, 'i-1', 'shh');
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `POST /api/v1/workspaces/${WS}/integrations`,
      `GET /api/v1/workspaces/${WS}/integrations/i-1`,
      `PATCH /api/v1/workspaces/${WS}/integrations/i-1`,
      `DELETE /api/v1/workspaces/${WS}/integrations/i-1`,
      `POST /api/v1/workspaces/${WS}/integrations/i-1/rotate-secret`,
    ]);
    expect(calls[4].opts).toMatchObject({ body: { secret: 'shh' } });
  });
});

describe('binding + event endpoints', () => {
  it('maps binding verbs', async () => {
    const { client, calls } = makeClient();
    await listBindings(client, WS, 'i-1');
    await createBinding(client, WS, 'i-1', { external_ref: 'owner/repo' });
    await patchBinding(client, WS, 'b-1', { status: 'disabled' });
    await deleteBinding(client, WS, 'b-1');
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `LIST /api/v1/workspaces/${WS}/integrations/i-1/bindings`,
      `POST /api/v1/workspaces/${WS}/integrations/i-1/bindings`,
      `PATCH /api/v1/workspaces/${WS}/integration-bindings/b-1`,
      `DELETE /api/v1/workspaces/${WS}/integration-bindings/b-1`,
    ]);
  });

  it('maps event ledger listing with filters', async () => {
    const { client, calls } = makeClient();
    await listIntegrationEvents(client, WS, 'i-1', {
      signature_status: 'invalid',
      process_status: 'rejected',
      cursor: 'c',
      limit: 5,
    });
    expect(calls[0]).toMatchObject({
      method: 'LIST',
      path: `/api/v1/workspaces/${WS}/integrations/i-1/events`,
      opts: {
        query: { signature_status: 'invalid', process_status: 'rejected', cursor: 'c', limit: 5 },
      },
    });
  });
});

describe('subscription + delivery endpoints', () => {
  it('maps subscription verbs', async () => {
    const { client, calls } = makeClient();
    await listSubscriptions(client, WS);
    await createSubscription(client, WS, { url: 'https://x.com/hook' });
    await getSubscription(client, WS, 's-1');
    await patchSubscription(client, WS, 's-1', { status: 'paused' });
    await deleteSubscription(client, WS, 's-1');
    await resumeSubscription(client, WS, 's-1');
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `LIST /api/v1/workspaces/${WS}/webhook-subscriptions`,
      `POST /api/v1/workspaces/${WS}/webhook-subscriptions`,
      `GET /api/v1/workspaces/${WS}/webhook-subscriptions/s-1`,
      `PATCH /api/v1/workspaces/${WS}/webhook-subscriptions/s-1`,
      `DELETE /api/v1/workspaces/${WS}/webhook-subscriptions/s-1`,
      `POST /api/v1/workspaces/${WS}/webhook-subscriptions/s-1/resume`,
    ]);
  });

  it('maps delivery listing + retry', async () => {
    const { client, calls } = makeClient();
    await listDeliveries(client, WS, 's-1', { state: 'failed', limit: 3 });
    await retryDelivery(client, WS, 's-1', 'd-1');
    expect(calls[0]).toMatchObject({
      method: 'LIST',
      path: `/api/v1/workspaces/${WS}/webhook-subscriptions/s-1/deliveries`,
      opts: { query: { state: 'failed', limit: 3 } },
    });
    expect(calls[1]).toMatchObject({
      method: 'POST',
      path: `/api/v1/workspaces/${WS}/webhook-subscriptions/s-1/deliveries/d-1/retry`,
    });
  });
});

describe('external identity endpoints', () => {
  it('maps identity verbs', async () => {
    const { client, calls } = makeClient();
    await listExternalIdentities(client, WS);
    await linkExternalIdentity(client, WS, {
      provider: 'slack',
      integration_id: 'i-1',
      external_user_key: 'U1',
    });
    await confirmExternalIdentity(client, WS, { provider: 'slack', integration_id: 'i-1', code: '123' });
    await unlinkExternalIdentity(client, WS, 'id-1');
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `LIST /api/v1/workspaces/${WS}/external-identities`,
      `POST /api/v1/workspaces/${WS}/external-identities:link`,
      `POST /api/v1/workspaces/${WS}/external-identities:link-confirm`,
      `DELETE /api/v1/workspaces/${WS}/external-identities/id-1`,
    ]);
    expect(calls[1].opts).toMatchObject({
      body: { provider: 'slack', integration_id: 'i-1', external_user_key: 'U1' },
    });
  });
});

describe('vcs link endpoints', () => {
  it('maps vcs link verbs', async () => {
    const { client, calls } = makeClient();
    await createVcsLink(client, {
      integration_id: 'i-1',
      vcs_ref: { type: 'pull_request', id: 'owner/repo#1' },
      mesh_entity_type: 'issue',
      issue_id: 'issue-1',
    });
    await deleteVcsLink(client, 'l-1');
    await listIssueVcsLinks(client, 'issue-1');
    await resolveVcsLink(client, {
      integration_id: 'i-1',
      source_text: 'WEB-1',
      vcs_ref: { type: 'commit', id: 'abc' },
    });
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      'POST /api/v1/integrations/vcs/links',
      'DELETE /api/v1/integrations/vcs/links/l-1',
      'LIST /api/v1/issues/issue-1/vcs-links',
      'POST /api/v1/integrations/vcs/resolve',
    ]);
  });
});
