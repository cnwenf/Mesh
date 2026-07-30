import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BADGE_TONE_ICONS, Badge } from '../components/Badge';
import type { BadgeTone } from '../components/Badge';

describe('Badge(§7.2 图标+文案,颜色不是唯一信号)', () => {
  it('默认 neutral + sm + tone 默认图标', () => {
    render(<Badge>草稿</Badge>);
    const badge = screen.getByText('草稿').closest('span')!;
    expect(badge).toHaveClass('mesh-badge', 'mesh-badge--neutral');
    expect(badge.querySelector('svg')).not.toBeNull();
  });

  it('各 tone 均渲染对应类名与默认图标', () => {
    const tones: BadgeTone[] = ['neutral', 'info', 'success', 'warning', 'danger', 'accent'];
    for (const tone of tones) {
      const { unmount } = render(<Badge tone={tone}>x</Badge>);
      const badge = screen.getByText('x').closest('span')!;
      expect(badge).toHaveClass(`mesh-badge--${tone}`);
      expect(badge.querySelector('svg')).not.toBeNull();
      expect(BADGE_TONE_ICONS[tone]).toBeTruthy();
      unmount();
    }
  });

  it('icon=null 关闭图标;自定义 icon 覆盖默认', () => {
    const { rerender } = render(<Badge icon={null}>无图标</Badge>);
    expect(screen.getByText('无图标').closest('span')!.querySelector('svg')).toBeNull();
    rerender(
      <Badge icon="sparkle" tone="success">
        自定义
      </Badge>,
    );
    const paths = screen.getByText('自定义').closest('span')!.querySelectorAll('path');
    expect(paths.length).toBeGreaterThan(0);
  });

  it('md 尺寸加高类名', () => {
    render(<Badge size="md">大徽标</Badge>);
    expect(screen.getByText('大徽标').closest('span')).toHaveClass('mesh-badge--md');
  });

  it('className 透传', () => {
    render(<Badge className="custom">t</Badge>);
    expect(screen.getByText('t').closest('span')).toHaveClass('mesh-badge', 'custom');
  });
});
