/** `agent.trigger_skipped` 前端呈现契约(agent.md §3.6/§4.6)。 */
import type { ToastTone } from '../design';
import type { RealtimeEventFrame } from '../types/realtime';

const REASON_MESSAGE_KEYS = {
  agent_not_found: 'agents.triggerSkipped.agent_not_found',
  lifecycle_not_active: 'agents.triggerSkipped.lifecycle_not_active',
  member_not_active: 'agents.triggerSkipped.member_not_active',
  trigger_on_assign_disabled: 'agents.triggerSkipped.trigger_on_assign_disabled',
  rate_limited: 'agents.triggerSkipped.rate_limited',
  chain_depth_exceeded: 'agents.triggerSkipped.chain_depth_exceeded',
  visibility_private: 'agents.triggerSkipped.visibility_private',
} as const;

export interface AgentTriggerSkippedNotice {
  readonly agentId: string | null;
  readonly issueId: string | null;
  readonly trigger: string;
  readonly reason: string;
  readonly messageKey: string;
  readonly tone: ToastTone;
}

function optionalString(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

export function parseAgentTriggerSkipped(
  frame: RealtimeEventFrame,
  workspaceId: string,
): AgentTriggerSkippedNotice | null {
  if (
    frame.channel !== `workspace:${workspaceId}:agents` ||
    frame.event !== 'agent.trigger_skipped'
  ) {
    return null;
  }
  const payload = frame.payload;
  const trigger = optionalString(payload.trigger);
  const reason = optionalString(payload.reason);
  if (trigger === null || reason === null) return null;
  const knownKey = REASON_MESSAGE_KEYS[reason as keyof typeof REASON_MESSAGE_KEYS];
  return {
    agentId: optionalString(payload.agent_id),
    issueId: optionalString(payload.issue_id),
    trigger,
    reason,
    messageKey: knownKey ?? 'agents.triggerSkipped.unknown',
    tone: reason === 'trigger_on_assign_disabled' ? 'info' : 'warn',
  };
}
