/**
 * 看板上的 execution 活跃态投影。
 *
 * 列表响应负责重连后的权威快照，workspace execution 帧负责低延迟更新。
 * 状态按 execution id 保存而非按 issue 覆盖，避免同一 issue 的两个并发运行中
 * 一个先终止时误清除另一个仍活跃的运行。
 */
import type { RealtimeEventFrame } from '../../types/realtime';

export interface BoardExecutionPresenceItem {
  readonly id: string;
  readonly issue_id: string;
  readonly status: string;
}

export type BoardExecutionPresence = Readonly<Record<string, BoardExecutionPresenceItem>>;

const ACTIVE_STATUSES = new Set([
  'queued',
  'claimed',
  'running',
  'cancelling',
  'awaiting_approval',
]);

const EVENT_STATUS: Readonly<Record<string, string>> = {
  'execution.queued': 'queued',
  'execution.requeued': 'queued',
  'execution.claimed': 'claimed',
  'execution.started': 'running',
  'execution.progress': 'running',
  'execution.cancelling': 'cancelling',
  'execution.awaiting_approval': 'awaiting_approval',
};

const TERMINAL_EVENTS = new Set([
  'execution.completed',
  'execution.failed',
  'execution.timeout',
  'execution.cancelled',
]);

export function executionPresenceFromList(
  executions: readonly {
    readonly id: string;
    readonly issue_id: string | null;
    readonly status: string;
  }[],
): BoardExecutionPresence {
  const next: Record<string, BoardExecutionPresenceItem> = {};
  for (const execution of executions) {
    if (execution.issue_id === null || !ACTIVE_STATUSES.has(execution.status)) continue;
    next[execution.id] = {
      id: execution.id,
      issue_id: execution.issue_id,
      status: execution.status,
    };
  }
  return next;
}

export function applyExecutionPresenceFrame(
  current: BoardExecutionPresence,
  frame: RealtimeEventFrame,
): BoardExecutionPresence {
  const payload = frame.payload;
  const executionId = payload.execution_id;
  if (typeof executionId !== 'string') return current;

  if (TERMINAL_EVENTS.has(frame.event)) {
    if (current[executionId] === undefined) return current;
    const next = { ...current };
    delete next[executionId];
    return next;
  }

  const status = EVENT_STATUS[frame.event];
  if (status === undefined) return current;
  const issueId =
    typeof payload.issue_id === 'string' ? payload.issue_id : current[executionId]?.issue_id;
  if (issueId === undefined) return current;
  const previous = current[executionId];
  if (previous?.issue_id === issueId && previous.status === status) return current;
  return {
    ...current,
    [executionId]: { id: executionId, issue_id: issueId, status },
  };
}

export function activeExecutionStatusByIssue(
  presence: BoardExecutionPresence,
): Readonly<Record<string, string>> {
  const result: Record<string, string> = {};
  for (const execution of Object.values(presence)) {
    result[execution.issue_id] = execution.status;
  }
  return result;
}
