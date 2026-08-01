/**
 * ApprovalCard 单卡测试(README §6.10 展示要求):直接渲染单卡覆盖页面级
 * 测试难以命中的展示分支——不可解析过期时间、本地过期 pending(惰性 reaper
 * 窗口)、无 headline、capability/permission 单边 chip、无待执行工具的续跑
 * 提示、已决定只读行。nowMs 取固定时刻保证确定性;i18n 键已并入目录,
 * 展示分支按 testid 定位、文案断言用真实 en 文案。
 */
import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { ApprovalCard, formatAbsolute } from '../ApprovalCard';
import type { Approval } from '../api';

/** 固定「当前时刻」:2026-07-30T12:00:00Z,过期相对时间断言不随墙钟漂移。 */
const NOW = Date.parse('2026-07-30T12:00:00Z');

const iso = (ms: number): string => new Date(ms).toISOString();

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: 'ap1',
    subject_type: 'tool_call',
    subject_execution_id: 'ex1',
    subject_task_id: null,
    status: 'pending',
    action_summary: {},
    requested_at: iso(NOW - 10 * 60000),
    expires_at: iso(NOW + 30 * 60000),
    decided_at: null,
    decision_comment: null,
    execution_status: null,
    ...overrides,
  };
}

describe('formatAbsolute', () => {
  it('formats a valid ISO string via the browser locale', () => {
    const input = '2026-07-30T12:00:00Z';
    expect(formatAbsolute(input)).toBe(new Date(input).toLocaleString());
  });

  it('returns the raw input when it cannot be parsed', () => {
    expect(formatAbsolute('not-a-date')).toBe('not-a-date');
  });
});

describe('ApprovalCard pending rendering', () => {
  it('renders headline, both permission chips, impact, cost, resume tool, relative expiry and wires the decide buttons', () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    const approval = makeApproval({
      action_summary: {
        action: 'shell.run',
        capability: 'shell',
        permission: 'execute',
        impact_scope: 'repo mesh',
        estimated_cost: '~2s',
        resume_context: { completed_steps: 3, pending_tool_call: 'rm -rf /tmp/x' },
      },
    });
    renderWithProviders(
      <ApprovalCard
        approval={approval}
        nowMs={NOW}
        onApprove={onApprove}
        onReject={onReject}
      />,
      { route: '/' },
    );

    expect(screen.getByTestId('approval-action-ap1')).toHaveTextContent('shell.run');
    expect(screen.getByText('Capability: shell')).toBeInTheDocument();
    expect(screen.getByText('Permission: execute')).toBeInTheDocument();
    expect(screen.getByTestId('approval-impact-ap1')).toBeInTheDocument();
    // 预估成本行渲染真实文案(占位符 {cost}=~2s)
    expect(screen.getByText('Est. cost: ~2s')).toBeInTheDocument();
    expect(screen.getByTestId('approval-expires-ap1')).toHaveAttribute(
      'title',
      formatAbsolute(iso(NOW + 30 * 60000)),
    );
    const resume = screen.getByTestId('approval-resume-ap1');
    // 待执行工具行渲染真实文案(占位符 {tool})
    expect(within(resume).getByText('Pending tool call: rm -rf /tmp/x')).toBeInTheDocument();
    expect(screen.getByTestId('approval-link-ap1')).toHaveAttribute('href', '/executions/ex1');

    fireEvent.click(screen.getByTestId('approval-approve-ap1'));
    expect(onApprove).toHaveBeenCalledWith('ap1');
    fireEvent.click(screen.getByTestId('approval-reject-ap1'));
    expect(onReject).toHaveBeenCalledWith(approval);
  });

  it('renders decide buttons safely without handlers and reflects the deciding state', () => {
    renderWithProviders(
      <ApprovalCard approval={makeApproval({ action_summary: { action: 'shell.run' } })} nowMs={NOW} isDeciding />,
      { route: '/' },
    );
    const approve = screen.getByTestId('approval-approve-ap1');
    // isLoading:禁用 + aria-busy
    expect(approve).toBeDisabled();
    expect(approve).toHaveAttribute('aria-busy', 'true');
    // 未提供回调时点击走 optional chaining 空路径,不抛错
    fireEvent.click(approve);
    fireEvent.click(screen.getByTestId('approval-reject-ap1'));
  });

  it('shows the raw expiry string with the past-relative fallback for an unparsable expiry', () => {
    renderWithProviders(
      <ApprovalCard approval={makeApproval({ expires_at: 'not-a-date' })} nowMs={NOW} />,
      { route: '/' },
    );
    const expires = screen.getByTestId('approval-expires-ap1');
    expect(expires).toHaveAttribute('title', 'not-a-date');
    // 相对时间回退到 past 分支(approvals.rel.past → 「any moment now」)
    expect(expires).toHaveTextContent('any moment now');
  });

  it('treats a pending approval past its expiry as expired: badge, relaunch link, no decide buttons', () => {
    renderWithProviders(
      <ApprovalCard approval={makeApproval({ expires_at: iso(NOW - 5 * 60000) })} nowMs={NOW} />,
      { route: '/' },
    );
    expect(screen.getByTestId('approval-expired-ap1')).toBeInTheDocument();
    expect(screen.getByTestId('approval-link-ap1')).toHaveTextContent('Relaunch');
    expect(screen.queryByTestId('approval-approve-ap1')).toBeNull();
    expect(screen.queryByTestId('approval-reject-ap1')).toBeNull();
  });

  it('omits the pending tool line when the resume context has no pending_tool_call', () => {
    renderWithProviders(
      <ApprovalCard
        approval={makeApproval({
          action_summary: { action: 'shell.run', resume_context: { completed_steps: 2 } },
        })}
        nowMs={NOW}
      />,
      { route: '/' },
    );
    const resume = screen.getByTestId('approval-resume-ap1');
    expect(resume).toHaveTextContent(
      'Resumes from the approval point as a new attempt: 2 steps completed.',
    );
    expect(within(resume).queryByText(/Pending tool call:/)).toBeNull();
  });

  it('omits the subject deep link when it cannot be resolved', () => {
    renderWithProviders(
      <ApprovalCard
        approval={makeApproval({ subject_execution_id: null, action_summary: { action: 'shell.run' } })}
        nowMs={NOW}
      />,
      { route: '/' },
    );
    expect(screen.queryByTestId('approval-link-ap1')).toBeNull();
  });

  it('omits the action headline when the summary has neither action nor plan_digest', () => {
    renderWithProviders(
      <ApprovalCard approval={makeApproval({ action_summary: { capability: 'shell' } })} nowMs={NOW} />,
      { route: '/' },
    );
    expect(screen.queryByTestId('approval-action-ap1')).toBeNull();
  });

  it('renders the capability chip and permission chip independently', () => {
    renderWithProviders(
      <>
        <ApprovalCard
          approval={makeApproval({
            id: 'cap-only',
            action_summary: { action: 'shell.run', capability: 'shell' },
          })}
          nowMs={NOW}
        />
        <ApprovalCard
          approval={makeApproval({
            id: 'perm-only',
            action_summary: { action: 'shell.run', permission: 'execute' },
          })}
          nowMs={NOW}
        />
      </>,
      { route: '/' },
    );
    const capCard = within(screen.getByTestId('approval-card-cap-only'));
    expect(capCard.getByText('Capability: shell')).toBeInTheDocument();
    expect(capCard.queryByText(/Permission:/)).toBeNull();

    const permCard = within(screen.getByTestId('approval-card-perm-only'));
    expect(permCard.getByText('Permission: execute')).toBeInTheDocument();
    expect(permCard.queryByText(/Capability:/)).toBeNull();
  });
});

describe('ApprovalCard decided rendering', () => {
  it('renders a rejected approval read-only with the absolute expiry and the decision comment', () => {
    renderWithProviders(
      <ApprovalCard
        approval={makeApproval({
          status: 'rejected',
          decided_at: iso(NOW - 60000),
          decision_comment: 'too risky',
        })}
        nowMs={NOW}
      />,
      { route: '/' },
    );
    expect(screen.getByTestId('approval-status-ap1')).toBeInTheDocument();
    // 决定留言行渲染真实文案(占位符 {comment})
    expect(screen.getByText('Comment: too risky')).toBeInTheDocument();
    expect(screen.queryByTestId('approval-approve-ap1')).toBeNull();
    // 非 pending:走绝对时间行(无相对过期 testid),subject 深链文案为 openSubject「Open」
    expect(screen.queryByTestId('approval-expires-ap1')).toBeNull();
    expect(screen.getByTestId('approval-link-ap1')).toHaveTextContent('Open');
  });
});
