/**
 * RunStatus 单测(design-quality.md §9.8):五态渲染、图标+文案双信号(非仅颜色)、
 * running 脉冲标记、failed 重试入口。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { RUN_STATUS_CONFIG, RunStatus } from '../RunStatus';
import type { RunStatusKind } from '../RunStatus';

const ALL_STATES: readonly RunStatusKind[] = ['queued', 'running', 'waiting', 'succeeded', 'failed'];

/** 各状态文案键在 en 目录下的实际渲染文案(测试 locale=en)。 */
const EXPECTED_TEXT: Readonly<Record<RunStatusKind, string>> = {
  queued: 'Queued',
  running: 'Running',
  waiting: 'Waiting for input',
  succeeded: 'Completed',
  failed: 'Run failed',
};

describe('RunStatus', () => {
  it.each(ALL_STATES)('renders the %s state with icon + text (non-color signal)', (status) => {
    renderWithProviders(<RunStatus status={status} />);
    const node = screen.getByTestId(`run-status-${status}`);
    // 状态文案(locale=en 的实际英文,经 RUN_STATUS_CONFIG.textKey 解析)
    expect(node.textContent).toContain(EXPECTED_TEXT[status]);
    // 图标存在(aria-hidden svg),确保不只靠颜色
    expect(node.querySelector('.mesh-run-status__icon')).not.toBeNull();
  });

  it.each(ALL_STATES)('applies the %s tone class', (status) => {
    renderWithProviders(<RunStatus status={status} />);
    const node = screen.getByTestId(`run-status-${status}`);
    expect(node.className).toContain(`mesh-run-status--${RUN_STATUS_CONFIG[status].tone}`);
  });

  it('renders the agent name alongside the status text', () => {
    renderWithProviders(<RunStatus status="running" agentName="code-reviewer" />);
    expect(screen.getByTestId('run-status-running').textContent).toContain('code-reviewer');
  });

  it('marks only the running state with the pulse modifier', () => {
    renderWithProviders(<RunStatus status="running" />);
    expect(screen.getByTestId('run-status-running').className).toContain('mesh-run-status--pulse');
  });

  it('shows a retry entry on failed when onRetry is provided and invokes it', () => {
    const onRetry = vi.fn();
    renderWithProviders(<RunStatus status="failed" onRetry={onRetry} retryLabel="Retry run" />);
    const retry = screen.getByTestId('run-status-retry');
    expect(retry.textContent).toBe('Retry run');
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('omits the retry entry on failed without onRetry', () => {
    renderWithProviders(<RunStatus status="failed" />);
    expect(screen.queryByTestId('run-status-retry')).toBeNull();
  });

  it('exposes a frozen config covering all five states', () => {
    expect(Object.keys(RUN_STATUS_CONFIG).sort()).toEqual([...ALL_STATES].sort());
    expect(Object.isFrozen(RUN_STATUS_CONFIG)).toBe(true);
  });
});
