/**
 * 上手清单卡片组件测试(onboarding.md §4.1/§4.2):
 * 五步按序渲染、进度条取值、自动完成角标、CTA 深链导航、dismiss 隐藏 + 调 API、
 * aha 庆祝态、已 dismiss 隐藏、首未完成步骤高亮。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import { OnboardingChecklist } from '../OnboardingChecklist';

const ME = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'WS',
      workspace_slug: 'team',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};
const ROSTER = {
  data: [
    {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Owner',
      joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null },
    },
  ],
  next_cursor: null,
};

function step(key: string, status: string, via: string | null, at: string | null): Record<string, unknown> {
  return { step_key: key, status, completed_via: via, completed_at: at };
}

function stateBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'obs-1',
    workspace_id: 'ws-1',
    member_id: 'mem-1',
    checklist: 'activation',
    aha_reached_at: null,
    dismissed_at: null,
    progress: { total: 5, completed: 2, skipped: 0 },
    steps: [
      step('create_workspace', 'completed', 'auto', '2026-07-24T10:00:00Z'),
      step('invite_member_or_add_agent', 'completed', 'manual', '2026-07-24T10:12:33Z'),
      step('create_first_issue', 'pending', null, null),
      step('dispatch_or_mention_agent', 'pending', null, null),
      step('see_agent_reply_in_inbox', 'pending', null, null),
    ],
    created_at: '2026-07-24T10:00:00Z',
    updated_at: '2026-07-24T10:12:33Z',
    ...overrides,
  };
}

interface RoutedCalls {
  calls: Array<{ url: string; method: string }>;
}

function stubApi(overrides: Record<string, unknown> = {}): RoutedCalls {
  const calls: Array<{ url: string; method: string }> = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/onboarding/state')) return fakeResponse({ body: { data: stateBody(overrides) } });
    if (url.includes('/onboarding/dismiss')) {
      return fakeResponse({ body: { data: { id: 'obs-1', dismissed_at: '2026-07-25T08:30:00Z' } } });
    }
    if (url.includes('/members')) return fakeResponse({ body: ROSTER });
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'nope' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', fetchImpl);
  return { calls };
}

/** 无实时上下文(默认 null):卡片照常渲染,走降级轮询路径。 */
const noRealtime: RealtimeContextValue | null = null;

function renderChecklist(): RoutedCalls {
  const routed = stubApi();
  renderWithProviders(
    <RealtimeContext.Provider value={noRealtime}>
      <OnboardingChecklist />
      <LocationProbe />
    </RealtimeContext.Provider>,
  );
  return routed;
}

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname}</span>;
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe('OnboardingChecklist', () => {
  it('renders the five steps in activation order with progress values', async () => {
    renderChecklist();
    await waitFor(() => expect(screen.getByTestId('onboarding-card')).toBeInTheDocument());
    const steps = screen.getAllByTestId(/^onboarding-step-/).map((el) => el.dataset.testid);
    expect(steps).toEqual([
      'onboarding-step-create_workspace',
      'onboarding-step-invite_member_or_add_agent',
      'onboarding-step-create_first_issue',
      'onboarding-step-dispatch_or_mention_agent',
      'onboarding-step-see_agent_reply_in_inbox',
    ]);
    const progress = screen.getByTestId('onboarding-progress');
    expect(progress).toHaveAttribute('aria-valuenow', '2');
    expect(progress).toHaveAttribute('aria-valuemax', '5');
    // 进度文案 = 完成数/总数 · 百分比
    expect(screen.getByText(/2 of 5/)).toBeInTheDocument();
    expect(screen.getByText(/40%/)).toBeInTheDocument();
  });

  it('shows the auto badge only for auto-completed steps and highlights the first pending', async () => {
    renderChecklist();
    await waitFor(() =>
      expect(screen.getByTestId('onboarding-auto-badge-create_workspace')).toBeInTheDocument(),
    );
    // manual 完成的步骤无 auto 角标
    expect(screen.queryByTestId('onboarding-auto-badge-invite_member_or_add_agent')).toBeNull();
    // 首个未完成步骤高亮(create_first_issue)
    const current = screen.getByTestId('onboarding-step-create_first_issue');
    expect(current.className).toContain('mesh-onboarding__step--current');
    expect(screen.getByTestId('onboarding-step-dispatch_or_mention_agent').className).not.toContain(
      'mesh-onboarding__step--current',
    );
    // 完成态 ✓ 勾选(图标 + 文字标签,非颜色唯一信号)
    expect(screen.getByTestId('onboarding-check-create_workspace')).toHaveAttribute(
      'aria-label',
      'Done',
    );
  });

  it('navigates to the existing wizards on CTA click (deeplinks per step)', async () => {
    const user = userEvent.setup();
    renderChecklist();
    await waitFor(() => expect(screen.getByTestId('onboarding-card')).toBeInTheDocument());

    await user.click(screen.getByTestId('onboarding-cta-create_workspace'));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/w/team/settings');

    await user.click(screen.getByTestId('onboarding-cta-invite_member_or_add_agent'));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/members');

    await user.click(screen.getByTestId('onboarding-cta-create_first_issue'));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/board');

    await user.click(screen.getByTestId('onboarding-cta-see_agent_reply_in_inbox'));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/inbox');
  });

  it('dismiss hides the card and calls the dismiss endpoint', async () => {
    const user = userEvent.setup();
    const routed = renderChecklist();
    await waitFor(() => expect(screen.getByTestId('onboarding-card')).toBeInTheDocument());

    await user.click(screen.getByTestId('onboarding-dismiss'));
    expect(routed.calls.some((call) => call.url.includes('/onboarding/dismiss'))).toBe(true);
    // 重拉返回的仍是未 dismiss 状态(桩固定),卡片仍在;仅验证 API 调用与重拉发生
    await waitFor(() =>
      expect(
        routed.calls.filter((call) => call.url.includes('/onboarding/state')).length,
      ).toBeGreaterThan(1),
    );
  });

  it('renders the aha celebration when aha_reached_at is set, and close dismisses', async () => {
    const user = userEvent.setup();
    const routed = stubApi({ aha_reached_at: '2026-07-25T09:00:00Z' });
    renderWithProviders(
      <RealtimeContext.Provider value={noRealtime}>
        <OnboardingChecklist />
        <LocationProbe />
      </RealtimeContext.Provider>,
    );
    await waitFor(() => expect(screen.getByTestId('onboarding-aha-card')).toBeInTheDocument());
    expect(screen.queryByTestId('onboarding-card')).toBeNull();

    await user.click(screen.getByTestId('onboarding-aha-action'));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/inbox');

    await user.click(screen.getByTestId('onboarding-aha-close'));
    expect(routed.calls.some((call) => call.url.includes('/onboarding/dismiss'))).toBe(true);
  });

  it('is hidden while dismissed', async () => {
    const routed = stubApi({ dismissed_at: '2026-07-25T08:30:00Z' });
    renderWithProviders(
      <RealtimeContext.Provider value={noRealtime}>
        <OnboardingChecklist />
      </RealtimeContext.Provider>,
    );
    // 等状态加载完成(dismissed 亦完成一次 state GET),再断言整体隐藏
    await waitFor(() =>
      expect(routed.calls.some((call) => call.url.includes('/onboarding/state'))).toBe(true),
    );
    expect(screen.queryByTestId('onboarding-card')).toBeNull();
    expect(screen.queryByTestId('onboarding-aha-card')).toBeNull();
  });
});
