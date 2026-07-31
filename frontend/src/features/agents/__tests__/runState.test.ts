/**
 * presenceToRunState 纯函数测试(design-quality.md §9.8 五态统一语言)。
 * 覆盖全部分支:无帧 unknown、running/queued/waiting 优先级、全 0 → idle。
 */
import { describe, expect, it } from 'vitest';
import { presenceToRunState } from '../runState';

describe('presenceToRunState', () => {
  it('无帧(null)→ unknown', () => {
    expect(presenceToRunState(null)).toBe('unknown');
  });

  it('running > 0 → running(优先级最高)', () => {
    expect(presenceToRunState({ running: 1, queued: 0, awaiting: 0 })).toBe('running');
  });

  it('running 同时含 queued/awaiting 时仍为 running', () => {
    expect(presenceToRunState({ running: 2, queued: 3, awaiting: 4 })).toBe('running');
  });

  it('无 running 而 queued > 0 → queued', () => {
    expect(presenceToRunState({ running: 0, queued: 1, awaiting: 0 })).toBe('queued');
  });

  it('queued 同时含 awaiting 时优先 queued', () => {
    expect(presenceToRunState({ running: 0, queued: 1, awaiting: 2 })).toBe('queued');
  });

  it('仅 awaiting > 0 → waiting', () => {
    expect(presenceToRunState({ running: 0, queued: 0, awaiting: 1 })).toBe('waiting');
  });

  it('三者全 0 → idle', () => {
    expect(presenceToRunState({ running: 0, queued: 0, awaiting: 0 })).toBe('idle');
  });
});
