/**
 * approvals/summary 纯函数测试(README §6.10 展示层解析):
 * subject 深链、action_summary 各字段解析、影响范围拍平、相对时间。
 */
import { describe, expect, it } from 'vitest';
import type { Approval } from '../api';
import {
  actionHeadline,
  autopilotRunIdOf,
  estimatedCostOf,
  formatImpactScope,
  isExpiredApproval,
  pendingToolCallText,
  permissionChips,
  relativeParts,
  resumeCompletedSteps,
  subjectIcon,
  subjectLabelKey,
  subjectLink,
} from '../summary';

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: 'ap1',
    subject_type: 'tool_call',
    subject_execution_id: 'ex1',
    subject_task_id: null,
    status: 'pending',
    action_summary: {},
    requested_at: '2026-07-30T00:00:00Z',
    expires_at: '2026-07-30T01:00:00Z',
    decided_at: null,
    decision_comment: null,
    execution_status: null,
    ...overrides,
  };
}

describe('subject meta', () => {
  it('maps subject types to distinct icons and label keys', () => {
    expect(subjectIcon('tool_call')).toBe('agent');
    expect(subjectIcon('autopilot_action')).toBe('refresh');
    expect(subjectIcon('squad_plan')).toBe('board');
    expect(subjectLabelKey('tool_call')).toBe('approvals.subject.toolCall');
    expect(subjectLabelKey('autopilot_action')).toBe('approvals.subject.autopilotAction');
    expect(subjectLabelKey('squad_plan')).toBe('approvals.subject.squadPlan');
  });
});

describe('actionHeadline', () => {
  it('prefers action, falls back to plan_digest, else null', () => {
    expect(actionHeadline({ action: 'shell.run' })).toBe('shell.run');
    expect(actionHeadline({ plan_digest: 'split into 3' })).toBe('split into 3');
    expect(actionHeadline({ action: '   ' })).toBeNull();
    expect(actionHeadline({})).toBeNull();
  });
});

describe('permissionChips', () => {
  it('returns trimmed capability/permission, null when absent or blank', () => {
    expect(permissionChips({ capability: 'shell', permission: 'execute' })).toEqual({
      capability: 'shell',
      permission: 'execute',
    });
    expect(permissionChips({ capability: '  ' })).toEqual({ capability: null, permission: null });
    expect(permissionChips({})).toEqual({ capability: null, permission: null });
  });
});

describe('formatImpactScope', () => {
  it('passes strings through and flattens objects to k=v pairs', () => {
    expect(formatImpactScope('issue 12 subtree')).toBe('issue 12 subtree');
    expect(formatImpactScope({ trigger_type: 'cron', depth: 2 })).toBe('trigger_type=cron · depth=2');
    expect(formatImpactScope('')).toBeNull();
    expect(formatImpactScope({})).toBeNull();
    expect(formatImpactScope(undefined)).toBeNull();
    expect(formatImpactScope({ nested: { a: 1 } })).toBeNull();
  });
});

describe('estimatedCostOf', () => {
  it('returns the cost string or null', () => {
    expect(estimatedCostOf({ estimated_cost: '~2s' })).toBe('~2s');
    expect(estimatedCostOf({ estimated_cost: ' ' })).toBeNull();
    expect(estimatedCostOf({})).toBeNull();
  });
});

describe('resume context parsing', () => {
  it('extracts completed steps only when a finite number', () => {
    expect(resumeCompletedSteps({ resume_context: { completed_steps: 3 } })).toBe(3);
    expect(resumeCompletedSteps({ resume_context: { completed_steps: null } })).toBeNull();
    expect(resumeCompletedSteps({})).toBeNull();
  });

  it('serializes object pending_tool_call and trims strings', () => {
    expect(pendingToolCallText({ resume_context: { pending_tool_call: 'rm -rf /tmp' } })).toBe(
      'rm -rf /tmp',
    );
    expect(pendingToolCallText({ resume_context: { pending_tool_call: { name: 'shell' } } })).toBe(
      '{"name":"shell"}',
    );
    expect(pendingToolCallText({ resume_context: { pending_tool_call: '  ' } })).toBeNull();
    expect(pendingToolCallText({})).toBeNull();
  });
});

describe('subjectLink', () => {
  it('links tool_call to the execution page', () => {
    expect(subjectLink(makeApproval())).toBe('/executions/ex1');
    expect(subjectLink(makeApproval({ subject_execution_id: null }))).toBeNull();
  });

  it('links autopilot_action via run_id (top-level or detail)', () => {
    const topLevel = makeApproval({
      subject_type: 'autopilot_action',
      subject_execution_id: null,
      action_summary: { run_id: 'run1' },
    });
    expect(subjectLink(topLevel)).toBe('/autopilots/runs/run1');
    const inDetail = makeApproval({
      subject_type: 'autopilot_action',
      subject_execution_id: null,
      action_summary: { detail: { run_id: 'run2' } },
    });
    expect(autopilotRunIdOf(inDetail)).toBe('run2');
    expect(subjectLink(inDetail)).toBe('/autopilots/runs/run2');
    expect(
      subjectLink(makeApproval({ subject_type: 'autopilot_action', subject_execution_id: null })),
    ).toBeNull();
  });

  it('links squad_plan only when squad id (detail) and task id are resolvable', () => {
    const resolvable = makeApproval({
      subject_type: 'squad_plan',
      subject_execution_id: null,
      subject_task_id: 't1',
      action_summary: { detail: { squad_id: 'sq1' } },
    });
    expect(subjectLink(resolvable)).toBe('/squads/sq1/tasks/t1');
    const noSquad = makeApproval({
      subject_type: 'squad_plan',
      subject_execution_id: null,
      subject_task_id: 't1',
    });
    expect(subjectLink(noSquad)).toBeNull();
  });
});

describe('isExpiredApproval', () => {
  const now = Date.parse('2026-07-30T00:30:00Z');

  it('treats expired status as expired regardless of time', () => {
    expect(isExpiredApproval(makeApproval({ status: 'expired' }), now)).toBe(true);
  });

  it('treats pending past expires_at as expired (lazy reaper window)', () => {
    const past = makeApproval({ expires_at: '2026-07-30T00:10:00Z' });
    expect(isExpiredApproval(past, now)).toBe(true);
    const future = makeApproval({ expires_at: '2026-07-30T01:00:00Z' });
    expect(isExpiredApproval(future, now)).toBe(false);
  });

  it('does not flag decided approvals as expired; invalid dates stay unexpired', () => {
    expect(isExpiredApproval(makeApproval({ status: 'approved' }), now)).toBe(false);
    expect(isExpiredApproval(makeApproval({ expires_at: 'garbage' }), now)).toBe(false);
  });
});

describe('relativeParts', () => {
  const now = Date.parse('2026-07-30T00:00:00Z');

  it('buckets into minutes/hours/days and flags past targets', () => {
    expect(relativeParts('2026-07-30T00:15:00Z', now)).toEqual({
      value: 15,
      unit: 'minute',
      past: false,
    });
    expect(relativeParts('2026-07-30T03:00:00Z', now)).toEqual({ value: 3, unit: 'hour', past: false });
    expect(relativeParts('2026-08-01T00:00:00Z', now)).toEqual({ value: 2, unit: 'day', past: false });
    expect(relativeParts('2026-07-29T23:00:00Z', now)).toEqual({
      value: 0,
      unit: 'minute',
      past: true,
    });
    expect(relativeParts('not-a-date', now)).toEqual({ value: 0, unit: 'minute', past: true });
  });
});
