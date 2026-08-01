import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RUN_STATE_ICONS, RUN_STATE_TONES, RunStateBadge } from '../patterns/RunStateBadge';
import type { RunState } from '../patterns/RunStateBadge';

const ALL_STATES: readonly RunState[] = [
  'queued',
  'running',
  'waiting',
  'succeeded',
  'failed',
  'idle',
  'unknown',
];

describe('RunStateBadge(design-quality.md §9.8 运行反馈五态统一语言)', () => {
  it('渲染文案 + data-state + tone 类名包裹', () => {
    render(<RunStateBadge state="running" label="运行中" />);
    const wrapper = screen.getByText('运行中').closest('.mesh-run-state-badge')!;
    expect(wrapper).toHaveAttribute('data-state', 'running');
    expect(wrapper).toHaveClass('mesh-run-state-badge--running');
    // tone 经 Badge 落地为 accent
    expect(wrapper.querySelector('.mesh-badge--accent')).not.toBeNull();
  });

  it('七态全部映射到固定 tone(§9.8 统一语言单一事实源)', () => {
    expect(RUN_STATE_TONES).toEqual({
      queued: 'info',
      running: 'accent',
      waiting: 'warning',
      succeeded: 'success',
      failed: 'danger',
      idle: 'neutral',
      unknown: 'neutral',
    });
    for (const state of ALL_STATES) {
      const { unmount } = render(<RunStateBadge state={state} label={`s-${state}`} />);
      const wrapper = screen.getByText(`s-${state}`).closest('.mesh-run-state-badge')!;
      expect(wrapper).toHaveAttribute('data-state', state);
      expect(wrapper.querySelector(`.mesh-badge--${RUN_STATE_TONES[state]}`)).not.toBeNull();
      unmount();
    }
  });

  it('unknown 态带 info 图标;其余态使用 tone 默认图标(经 RUN_STATE_ICONS 固定)', () => {
    expect(RUN_STATE_ICONS.unknown).toBe('info');
    for (const state of ALL_STATES) {
      if (state !== 'unknown') expect(RUN_STATE_ICONS[state]).toBeNull();
    }
    const { unmount } = render(<RunStateBadge state="unknown" label="状态未知" />);
    // unknown:显式 info 图标(中性「未知」形,配合文案,不以颜色表义)
    expect(screen.getByText('状态未知').closest('.mesh-badge')!.querySelector('svg')).not.toBeNull();
    unmount();
    render(<RunStateBadge state="failed" label="失败" />);
    // failed:RUN_STATE_ICONS=null → Badge 回退 tone 默认图标(error 形)
    expect(screen.getByText('失败').closest('.mesh-badge')!.querySelector('svg')).not.toBeNull();
  });

  it('size 透传 Badge(md 加高)', () => {
    render(<RunStateBadge state="succeeded" label="已完成" size="md" />);
    expect(screen.getByText('已完成').closest('.mesh-badge')).toHaveClass('mesh-badge--md');
  });

  it('自定义 className 合并到包裹层', () => {
    render(<RunStateBadge state="queued" label="已排队" className="my-extra" />);
    expect(screen.getByText('已排队').closest('.mesh-run-state-badge')).toHaveClass('my-extra');
  });
});
