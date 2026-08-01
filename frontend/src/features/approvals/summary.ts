/**
 * 审批展示层纯函数(README §6.10 展示要求):subject 元信息、action_summary
 * 解析、影响范围格式化、续跑提示、subject 深链与相对过期时间。
 * 全部无副作用、可单测;UI 文案键在组件侧经 t() 装配。
 */
import type { IconName } from '../../design';
import type { Approval, ApprovalActionSummary, ApprovalSubjectType } from './api';

/** subject 类型 → 图标 + 文案键(icon + 文本,颜色非唯一信号)。 */
export function subjectIcon(kind: ApprovalSubjectType): IconName {
  if (kind === 'tool_call') return 'agent';
  if (kind === 'autopilot_action') return 'refresh';
  return 'board';
}

export function subjectLabelKey(kind: ApprovalSubjectType): string {
  if (kind === 'tool_call') return 'approvals.subject.toolCall';
  if (kind === 'autopilot_action') return 'approvals.subject.autopilotAction';
  return 'approvals.subject.squadPlan';
}

/** 动作摘要行:优先 action;无则按 subject 回退到 plan_digest。 */
export function actionHeadline(summary: ApprovalActionSummary): string | null {
  if (typeof summary.action === 'string' && summary.action.trim() !== '') {
    return summary.action;
  }
  if (typeof summary.plan_digest === 'string' && summary.plan_digest.trim() !== '') {
    return summary.plan_digest;
  }
  return null;
}

/** 所需权限 chip 值:capability / permission(缺则 null,不渲染)。 */
export function permissionChips(
  summary: ApprovalActionSummary,
): { readonly capability: string | null; readonly permission: string | null } {
  const capability =
    typeof summary.capability === 'string' && summary.capability.trim() !== ''
      ? summary.capability
      : null;
  const permission =
    typeof summary.permission === 'string' && summary.permission.trim() !== ''
      ? summary.permission
      : null;
  return { capability, permission };
}

/** 影响范围:字符串原样;对象拍平为 k=v 连接;空/其他 → null。 */
export function formatImpactScope(scope: ApprovalActionSummary['impact_scope']): string | null {
  if (typeof scope === 'string') {
    return scope.trim() === '' ? null : scope;
  }
  if (typeof scope === 'object' && scope !== null) {
    const pairs = Object.entries(scope)
      .filter(([, v]) => typeof v === 'string' || typeof v === 'number')
      .map(([k, v]) => `${k}=${String(v)}`);
    return pairs.length > 0 ? pairs.join(' · ') : null;
  }
  return null;
}

/** 预估成本字符串(缺则 null)。 */
export function estimatedCostOf(summary: ApprovalActionSummary): string | null {
  return typeof summary.estimated_cost === 'string' && summary.estimated_cost.trim() !== ''
    ? summary.estimated_cost
    : null;
}

/** 续跑已完成步数(resume_context.completed_steps;缺则 null)。 */
export function resumeCompletedSteps(summary: ApprovalActionSummary): number | null {
  const steps = summary.resume_context?.completed_steps;
  return typeof steps === 'number' && Number.isFinite(steps) ? steps : null;
}

/** 待执行工具调用摘要(字符串原样;对象 JSON 化;缺则 null)。 */
export function pendingToolCallText(summary: ApprovalActionSummary): string | null {
  const pending = summary.resume_context?.pending_tool_call;
  if (typeof pending === 'string') return pending.trim() === '' ? null : pending;
  if (typeof pending === 'object' && pending !== null) return JSON.stringify(pending);
  return null;
}

function stringField(source: Record<string, unknown> | undefined, key: string): string | null {
  const value = source?.[key];
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/** autopilot_action 的 run id:优先顶层 run_id,回退 detail.run_id。 */
export function autopilotRunIdOf(approval: Approval): string | null {
  const summary = approval.action_summary;
  return (
    (typeof summary.run_id === 'string' && summary.run_id.length > 0 ? summary.run_id : null) ??
    stringField(summary.detail, 'run_id')
  );
}

/** subject 深链:能解析多少解析多少(§6.10 关联执行深链);不可解析 → null。 */
export function subjectLink(approval: Approval): string | null {
  if (approval.subject_type === 'tool_call') {
    return approval.subject_execution_id !== null
      ? `/executions/${approval.subject_execution_id}`
      : null;
  }
  if (approval.subject_type === 'autopilot_action') {
    const runId = autopilotRunIdOf(approval);
    return runId !== null ? `/autopilots/runs/${runId}` : null;
  }
  const squadId = stringField(approval.action_summary.detail, 'squad_id');
  if (squadId !== null && approval.subject_task_id !== null) {
    return `/squads/${squadId}/tasks/${approval.subject_task_id}`;
  }
  return null;
}

/** 是否已过期:服务端 expired 态,或 pending 但过期时间已过(reaper 惰性窗口)。 */
export function isExpiredApproval(approval: Approval, nowMs: number): boolean {
  if (approval.status === 'expired') return true;
  if (approval.status !== 'pending') return false;
  const expiry = Date.parse(approval.expires_at);
  return !Number.isNaN(expiry) && expiry < nowMs;
}

export type RelativeUnit = 'minute' | 'hour' | 'day';

export interface RelativeParts {
  readonly value: number;
  readonly unit: RelativeUnit;
  /** 目标时刻已过去 */
  readonly past: boolean;
}

/** 相对时间部件(最小 1 分钟);供组件按单位键本地化,保持纯函数。 */
export function relativeParts(targetIso: string, nowMs: number): RelativeParts {
  const target = Date.parse(targetIso);
  if (Number.isNaN(target)) return { value: 0, unit: 'minute', past: true };
  const deltaMinutes = Math.round((target - nowMs) / 60000);
  if (deltaMinutes <= 0) return { value: 0, unit: 'minute', past: true };
  if (deltaMinutes < 60) return { value: deltaMinutes, unit: 'minute', past: false };
  const hours = Math.floor(deltaMinutes / 60);
  if (hours < 24) return { value: hours, unit: 'hour', past: false };
  return { value: Math.floor(hours / 24), unit: 'day', past: false };
}
