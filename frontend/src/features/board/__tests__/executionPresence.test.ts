import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import {
  activeExecutionStatusByIssue,
  applyExecutionPresenceFrame,
  executionPresenceFromList,
} from '../executionPresence';

function frame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'workspace:ws:executions', seq: 1, event, payload };
}

describe('board execution presence projection', () => {
  it('hydrates only active issue executions from the authoritative list', () => {
    const state = executionPresenceFromList([
      { id: 'e-queued', issue_id: 'i-1', status: 'queued' },
      { id: 'e-running', issue_id: 'i-2', status: 'running' },
      { id: 'e-done', issue_id: 'i-3', status: 'completed' },
      { id: 'e-no-issue', issue_id: null, status: 'running' },
    ]);

    expect(activeExecutionStatusByIssue(state)).toEqual({
      'i-1': 'queued',
      'i-2': 'running',
    });
  });

  it('keeps issue correlation across claim/start frames and clears only the terminal run', () => {
    let state = executionPresenceFromList([
      { id: 'e-1', issue_id: 'i-1', status: 'queued' },
      { id: 'e-2', issue_id: 'i-1', status: 'queued' },
    ]);
    state = applyExecutionPresenceFrame(
      state,
      frame('execution.claimed', { execution_id: 'e-1', runtime_id: 'r-1' }),
    );
    state = applyExecutionPresenceFrame(state, frame('execution.started', { execution_id: 'e-1' }));
    state = applyExecutionPresenceFrame(
      state,
      frame('execution.completed', { execution_id: 'e-1' }),
    );

    expect(activeExecutionStatusByIssue(state)).toEqual({ 'i-1': 'queued' });
  });

  it('uses a final logical execution id from queued frames and ignores malformed frames', () => {
    const initial = executionPresenceFromList([]);
    const queued = applyExecutionPresenceFrame(
      initial,
      frame('execution.queued', { execution_id: 'final-id', issue_id: 'issue-id' }),
    );
    const malformed = applyExecutionPresenceFrame(
      queued,
      frame('execution.started', { execution_id: 42 }),
    );

    expect(activeExecutionStatusByIssue(malformed)).toEqual({ 'issue-id': 'queued' });
    expect(malformed).toBe(queued);
  });

  it('keeps the same reference for irrelevant, incomplete, duplicate, and unknown terminal frames', () => {
    const initial = executionPresenceFromList([{ id: 'e-1', issue_id: 'i-1', status: 'running' }]);

    expect(
      applyExecutionPresenceFrame(initial, frame('execution.completed', { execution_id: 'x' })),
    ).toBe(initial);
    expect(
      applyExecutionPresenceFrame(initial, frame('issue.updated', { execution_id: 'e-1' })),
    ).toBe(initial);
    expect(
      applyExecutionPresenceFrame(initial, frame('execution.started', { execution_id: 'x' })),
    ).toBe(initial);
    expect(
      applyExecutionPresenceFrame(initial, frame('execution.started', { execution_id: 'e-1' })),
    ).toBe(initial);
  });
});
