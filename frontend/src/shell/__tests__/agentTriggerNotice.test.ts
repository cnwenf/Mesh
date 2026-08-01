import { describe, expect, it } from 'vitest';
import { parseAgentTriggerSkipped } from '../agentTriggerNotice';

describe('parseAgentTriggerSkipped(G17)', () => {
  it('只接受当前 workspace agents 频道的 trigger_skipped，并保留可导航 issue id', () => {
    expect(
      parseAgentTriggerSkipped(
        {
          op: 'event',
          seq: 8,
          channel: 'workspace:ws-1:agents',
          event: 'agent.trigger_skipped',
          payload: {
            agent_id: 'agent-1',
            issue_id: 'issue-1',
            trigger: 'assign',
            reason: 'lifecycle_not_active',
            trigger_event_id: 'evt-1',
          },
        },
        'ws-1',
      ),
    ).toEqual({
      agentId: 'agent-1',
      issueId: 'issue-1',
      trigger: 'assign',
      reason: 'lifecycle_not_active',
      messageKey: 'agents.triggerSkipped.lifecycle_not_active',
      tone: 'warn',
    });
  });

  it('未知 reason 使用通用可本地化提示；畸形/其他事件安全忽略', () => {
    expect(
      parseAgentTriggerSkipped(
        {
          op: 'event',
          seq: 9,
          channel: 'workspace:ws-1:agents',
          event: 'agent.trigger_skipped',
          payload: { agent_id: null, issue_id: null, trigger: 'mention', reason: 'new_reason' },
        },
        'ws-1',
      )?.messageKey,
    ).toBe('agents.triggerSkipped.unknown');
    expect(
      parseAgentTriggerSkipped(
        {
          op: 'event',
          seq: 10,
          channel: 'workspace:ws-2:agents',
          event: 'agent.trigger_skipped',
          payload: { trigger: 'assign', reason: 'rate_limited' },
        },
        'ws-1',
      ),
    ).toBeNull();
    expect(
      parseAgentTriggerSkipped(
        {
          op: 'event',
          seq: 11,
          channel: 'workspace:ws-1:agents',
          event: 'agent.updated',
          payload: {},
        },
        'ws-1',
      ),
    ).toBeNull();
  });

  it('用户显式关闭分配触发时呈现 info，空可选 id 不会泄漏到导航', () => {
    expect(
      parseAgentTriggerSkipped(
        {
          op: 'event',
          seq: 12,
          channel: 'workspace:ws-1:agents',
          event: 'agent.trigger_skipped',
          payload: {
            agent_id: '',
            issue_id: '',
            trigger: 'assign',
            reason: 'trigger_on_assign_disabled',
          },
        },
        'ws-1',
      ),
    ).toEqual({
      agentId: null,
      issueId: null,
      trigger: 'assign',
      reason: 'trigger_on_assign_disabled',
      messageKey: 'agents.triggerSkipped.trigger_on_assign_disabled',
      tone: 'info',
    });
  });

  it.each([
    { trigger: '', reason: 'rate_limited' },
    { trigger: 123, reason: 'rate_limited' },
    { trigger: 'assign', reason: '' },
    { trigger: 'assign', reason: null },
  ])('忽略 trigger/reason 不完整的 payload: $trigger/$reason', (payload) => {
    expect(
      parseAgentTriggerSkipped(
        {
          op: 'event',
          seq: 13,
          channel: 'workspace:ws-1:agents',
          event: 'agent.trigger_skipped',
          payload,
        },
        'ws-1',
      ),
    ).toBeNull();
  });
});
