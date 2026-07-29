/**
 * 上手引导 API 契约层测试(onboarding.md §3.1/§3.2):路径 / 方法 / 查询参数 / 请求体 / 频道名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  completeOnboardingStep,
  dismissOnboarding,
  getOnboardingState,
  onboardingChannel,
  resetOnboardingMember,
  restoreOnboarding,
} from '../api';

const STATE = {
  id: 'obs-1',
  workspace_id: 'ws-1',
  member_id: 'mem-1',
  checklist: 'activation',
  aha_reached_at: null,
  dismissed_at: null,
  progress: { total: 5, completed: 1, skipped: 0 },
  steps: [],
  created_at: '2026-07-24T10:00:00Z',
  updated_at: '2026-07-24T10:00:00Z',
};

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: STATE } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

it('builds the member-private channel name (onboarding.md §3.7)', () => {
  expect(onboardingChannel('mem-1')).toBe('member:mem-1:onboarding');
});

describe('endpoint surface', () => {
  it('reads state with workspace_id query', async () => {
    const state = await getOnboardingState(client, 'ws-1');
    expect(state.id).toBe('obs-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/onboarding/state?workspace_id=ws-1');
    expect(stub.calls[0].init?.method).toBe('GET');
  });

  it('completes a step via POST with workspace_id and empty body', async () => {
    stub = stubFetch(
      fakeResponse({
        body: {
          data: {
            step_key: 'create_first_issue',
            status: 'completed',
            completed_via: 'manual',
            completed_at: '2026-07-25T08:00:00Z',
          },
        },
      }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const step = await completeOnboardingStep(client, 'ws-1', 'create_first_issue');
    expect(step.completed_via).toBe('manual');
    expect(stub.calls[0].url).toBe(
      'http://api/api/v1/onboarding/steps/create_first_issue/complete?workspace_id=ws-1',
    );
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({});
  });

  it('dismisses the checklist', async () => {
    stub = stubFetch(
      fakeResponse({ body: { data: { id: 'obs-1', dismissed_at: '2026-07-25T08:30:00Z' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    const result = await dismissOnboarding(client, 'ws-1');
    expect(result.dismissed_at).toBe('2026-07-25T08:30:00Z');
    expect(stub.calls[0].url).toBe('http://api/api/v1/onboarding/dismiss?workspace_id=ws-1');
  });

  it('restores a dismissed checklist', async () => {
    stub = stubFetch(fakeResponse({ body: { data: { id: 'obs-1', dismissed_at: null } } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const result = await restoreOnboarding(client, 'ws-1');
    expect(result.dismissed_at).toBeNull();
    expect(stub.calls[0].url).toBe('http://api/api/v1/onboarding/restore?workspace_id=ws-1');
  });

  it('resets a member checklist via the nested admin path (onboarding.md §3.4)', async () => {
    const result = await resetOnboardingMember(client, 'ws-1', 'mem-9');
    expect(result.id).toBe('obs-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/onboarding/reset');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({
      member_id: 'mem-9',
      checklist: 'activation',
    });
  });
});
