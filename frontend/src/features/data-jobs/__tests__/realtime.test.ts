/**
 * data_job.updated 帧合并纯函数测试(import-export.md §3.11)。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { applyDataJobFrame } from '../realtime';
import type { DataJob } from '../types';

function makeJob(overrides: Partial<DataJob> = {}): DataJob {
  return {
    id: 'dj-1',
    workspace_id: 'ws-1',
    kind: 'import',
    entity_type: 'issues',
    format: 'csv',
    status: 'running',
    total_rows: 100,
    succeeded_rows: 10,
    failed_rows: 0,
    source_attachment_id: null,
    result_attachment_id: null,
    failure_reason: null,
    requested_by: 'm-1',
    mapping: { columns: [] },
    params: {},
    started_at: null,
    finished_at: null,
    created_at: '2026-07-28T00:00:00Z',
    updated_at: '2026-07-28T00:01:00Z',
    ...overrides,
  };
}

function frame(payload: unknown, event = 'data_job.updated'): RealtimeEventFrame {
  return {
    op: 'event',
    channel: 'data_job:dj-1',
    seq: 1,
    event,
    payload: payload as Record<string, unknown>,
  };
}

describe('applyDataJobFrame', () => {
  it('merges whitelisted fields by id', () => {
    const jobs = [makeJob()];
    const next = applyDataJobFrame(
      jobs,
      frame({
        id: 'dj-1',
        status: 'running',
        succeeded_rows: 42,
        updated_at: '2026-07-28T00:02:00Z',
      }),
    );
    expect(next).not.toBe(jobs);
    expect(next[0]?.succeeded_rows).toBe(42);
    expect(next[0]?.failed_rows).toBe(0); // untouched
    expect(next[0]?.updated_at).toBe('2026-07-28T00:02:00Z');
  });

  it('merges terminal state with result attachment and failure reason', () => {
    const next = applyDataJobFrame(
      [makeJob()],
      frame({
        id: 'dj-1',
        status: 'failed',
        failure_reason: 'source_changed',
        finished_at: '2026-07-28T00:03:00Z',
        updated_at: '2026-07-28T00:03:00Z',
      }),
    );
    expect(next[0]?.status).toBe('failed');
    expect(next[0]?.failure_reason).toBe('source_changed');
    expect(next[0]?.finished_at).toBe('2026-07-28T00:03:00Z');
  });

  it('returns the SAME reference for frames of other events', () => {
    const jobs = [makeJob()];
    expect(applyDataJobFrame(jobs, frame({ id: 'dj-1' }, 'issue.updated'))).toBe(jobs);
  });

  it('returns the same reference for other jobs (view reconciliation via REST)', () => {
    const jobs = [makeJob()];
    expect(
      applyDataJobFrame(
        jobs,
        frame({ id: 'dj-other', status: 'completed', updated_at: '2026-07-28T09:00:00Z' }),
      ),
    ).toBe(jobs);
  });

  it('skips stale frames (updated_at older than local)', () => {
    const jobs = [makeJob()];
    expect(
      applyDataJobFrame(
        jobs,
        frame({ id: 'dj-1', succeeded_rows: 5, updated_at: '2026-07-27T00:00:00Z' }),
      ),
    ).toBe(jobs);
  });

  it('ignores non-object payloads (defensive, no crash)', () => {
    const jobs = [makeJob()];
    expect(applyDataJobFrame(jobs, frame(null))).toBe(jobs);
    expect(applyDataJobFrame(jobs, frame('str'))).toBe(jobs);
    expect(applyDataJobFrame(jobs, frame([1, 2]))).toBe(jobs);
  });

  it('ignores frames without a string id', () => {
    const jobs = [makeJob()];
    expect(applyDataJobFrame(jobs, frame({ status: 'completed' }))).toBe(jobs);
    expect(applyDataJobFrame(jobs, frame({ id: 42 }))).toBe(jobs);
  });

  it('does not merge non-whitelisted fields and skips prototype-pollution keys', () => {
    const jobs = [makeJob()];
    const malicious = JSON.parse(
      '{"id": "dj-1", "mapping": {"columns": "x"}, "__proto__": {"hacked": true}, "updated_at": "2026-07-28T00:05:00Z"}',
    ) as Record<string, unknown>;
    const next = applyDataJobFrame(jobs, frame(malicious));
    expect(next[0]?.mapping).toEqual({ columns: [] }); // not overwritten
    expect(({} as Record<string, unknown>)['hacked']).toBeUndefined();
    expect(next[0]?.updated_at).toBe('2026-07-28T00:05:00Z');
  });

  it('returns same reference when the merged values are identical', () => {
    const jobs = [makeJob()];
    const next = applyDataJobFrame(
      jobs,
      frame({
        id: 'dj-1',
        succeeded_rows: 10, // same as local
        updated_at: '2026-07-28T00:01:00Z', // same as local
      }),
    );
    expect(next).toBe(jobs);
  });

  it('accepts frames without updated_at (no anti-rollback gate)', () => {
    const next = applyDataJobFrame([makeJob()], frame({ id: 'dj-1', succeeded_rows: 77 }));
    expect(next[0]?.succeeded_rows).toBe(77);
  });
});
