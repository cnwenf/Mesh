import { createElement } from 'react';
import { act, renderHook, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getWorkspace } from '../../api/workspace';
import { renderWithProviders } from '../../test-utils/render';
import {
  DEFAULT_WORKSPACE_FEATURE_FLAGS,
  deriveWorkspaceFeatureFlags,
  isAutopilotFeaturePath,
  isNavItemEnabled,
  useWorkspaceFeatureFlagsContext,
  useWorkspaceFeatureFlagsValue,
  WorkspaceFeatureGate,
  WorkspaceFeatureFlagsProvider,
} from '../featureFlags';

vi.mock('../../api/instance', () => ({ getApiClient: vi.fn(() => ({})) }));
vi.mock('../../api/workspace', () => ({ getWorkspace: vi.fn() }));

describe('workspace feature flags(G15)', () => {
  beforeEach(() => {
    vi.mocked(getWorkspace).mockReset();
  });

  it('缺失或畸形值沿用兼容默认，显式 false 关闭 autopilot', () => {
    expect(deriveWorkspaceFeatureFlags({})).toEqual(DEFAULT_WORKSPACE_FEATURE_FLAGS);
    expect(deriveWorkspaceFeatureFlags({ feature_flags: 'bad' })).toEqual(
      DEFAULT_WORKSPACE_FEATURE_FLAGS,
    );
    expect(deriveWorkspaceFeatureFlags({ feature_flags: { autopilot: false } })).toEqual({
      autopilot: false,
    });
    expect(deriveWorkspaceFeatureFlags({ feature_flags: { autopilot: true } })).toEqual({
      autopilot: true,
    });
    expect(deriveWorkspaceFeatureFlags({ feature_flags: { autopilot: 'false' } })).toEqual(
      DEFAULT_WORKSPACE_FEATURE_FLAGS,
    );
  });

  it('autopilot 关闭时只过滤自动值守入口，其他运行入口保持可见', () => {
    const flags = { autopilot: false } as const;
    expect(isNavItemEnabled('autopilots', flags)).toBe(false);
    expect(isNavItemEnabled('runtimes', flags)).toBe(true);
    expect(isNavItemEnabled('insights', flags)).toBe(true);
  });

  it.each([
    ['/autopilots', true],
    ['/autopilots/new', true],
    ['/autopilots/runs/run-1', true],
    ['/webhooks', true],
    ['/automation', true],
    ['/webhook-subscriptions', false],
    ['/runtimes', false],
    ['/w/acme/automations/autopilots', true],
    ['/w/acme/automations/autopilots/run-1', true],
    ['/w/acme/autopilots', false],
  ])('只把自动值守路由族纳入功能门控：%s', (path, expected) => {
    expect(isAutopilotFeaturePath(path)).toBe(expected);
  });

  it('直达关闭功能的路由时呈现可访问的禁用态，不挂载业务页面', () => {
    renderWithProviders(
      createElement(WorkspaceFeatureFlagsProvider, {
        value: { autopilot: false },
        children: createElement(WorkspaceFeatureGate, {
          flag: 'autopilot',
          children: createElement('div', { 'data-testid': 'autopilot-page' }),
        }),
      }),
    );
    expect(screen.getByTestId('feature-disabled')).toBeInTheDocument();
    expect(screen.queryByTestId('autopilot-page')).not.toBeInTheDocument();
  });

  it('默认 context 和开启态 gate 都正常挂载业务内容', () => {
    const context = renderHook(() => useWorkspaceFeatureFlagsContext());
    expect(context.result.current).toEqual(DEFAULT_WORKSPACE_FEATURE_FLAGS);

    renderWithProviders(
      createElement(WorkspaceFeatureFlagsProvider, {
        value: { autopilot: true },
        children: createElement(WorkspaceFeatureGate, {
          flag: 'autopilot',
          children: createElement('div', { 'data-testid': 'autopilot-page' }),
        }),
      }),
    );
    expect(screen.getByTestId('autopilot-page')).toBeInTheDocument();
    expect(screen.queryByTestId('feature-disabled')).not.toBeInTheDocument();
  });

  it('从当前工作区 detail 读取开关，无工作区时不发请求', async () => {
    vi.mocked(getWorkspace).mockResolvedValue({
      settings: { feature_flags: { autopilot: false } },
    } as unknown as Awaited<ReturnType<typeof getWorkspace>>);

    const hook = renderHook(({ workspaceId }) => useWorkspaceFeatureFlagsValue(workspaceId), {
      initialProps: { workspaceId: null as string | null },
    });
    expect(hook.result.current).toEqual(DEFAULT_WORKSPACE_FEATURE_FLAGS);
    expect(getWorkspace).not.toHaveBeenCalled();

    hook.rerender({ workspaceId: 'ws-1' });
    await waitFor(() => expect(hook.result.current.autopilot).toBe(false));
    expect(getWorkspace).toHaveBeenCalledTimes(1);
  });

  it('忽略已切走工作区的延迟响应，读取失败时保持兼容默认', async () => {
    let resolveWorkspace: (value: Awaited<ReturnType<typeof getWorkspace>>) => void = () =>
      undefined;
    vi.mocked(getWorkspace).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveWorkspace = resolve;
        }),
    );
    const delayed = renderHook(({ workspaceId }) => useWorkspaceFeatureFlagsValue(workspaceId), {
      initialProps: { workspaceId: 'ws-old' as string | null },
    });
    delayed.rerender({ workspaceId: null });
    await act(async () => {
      resolveWorkspace({
        settings: { feature_flags: { autopilot: false } },
      } as unknown as Awaited<ReturnType<typeof getWorkspace>>);
    });
    expect(delayed.result.current).toEqual(DEFAULT_WORKSPACE_FEATURE_FLAGS);

    vi.mocked(getWorkspace).mockRejectedValueOnce(new Error('offline'));
    const failed = renderHook(() => useWorkspaceFeatureFlagsValue('ws-offline'));
    await waitFor(() => expect(getWorkspace).toHaveBeenCalledWith(expect.anything(), 'ws-offline'));
    await act(async () => Promise.resolve());
    expect(failed.result.current).toEqual(DEFAULT_WORKSPACE_FEATURE_FLAGS);
  });
});
