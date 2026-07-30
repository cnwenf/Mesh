import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ICON_PATHS, Icon } from '../components/Icon';
import type { IconName } from '../components/Icon';

describe('Icon(§7.1 统一线性 SVG 图标)', () => {
  it('默认 20px、aria-hidden(伴随可见文案的默认用法)', () => {
    const { container } = render(<Icon name="search" />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveClass('mesh-icon', 'mesh-icon--20');
    expect(svg).toHaveAttribute('width', '20');
    expect(svg).toHaveAttribute('height', '20');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg.getAttribute('role')).toBeNull();
  });

  it('支持 16/24 尺寸档', () => {
    const { container } = render(
      <>
        <Icon name="check" size={16} />
        <Icon name="close" size={24} />
      </>,
    );
    expect(container.querySelector('.mesh-icon--16')).not.toBeNull();
    expect(container.querySelector('.mesh-icon--24')).not.toBeNull();
  });

  it('label 提供时 role=img 且有可访问名(独立图标按钮场景)', () => {
    render(<Icon name="warning" label="警告" />);
    expect(screen.getByRole('img', { name: '警告' })).toBeInTheDocument();
  });

  it('className 透传', () => {
    const { container } = render(<Icon name="plus" className="custom" />);
    expect(container.querySelector('svg')).toHaveClass('mesh-icon', 'custom');
  });

  it('每个图标名都有非空路径且可渲染(无空 path)', () => {
    const names = Object.keys(ICON_PATHS) as IconName[];
    expect(names.length).toBeGreaterThanOrEqual(28);
    for (const name of names) {
      expect(ICON_PATHS[name].length, `${name} 至少一条路径`).toBeGreaterThan(0);
      for (const d of ICON_PATHS[name]) {
        expect(d.length, `${name} 路径非空`).toBeGreaterThan(0);
      }
      const { container, unmount } = render(<Icon name={name} />);
      expect(container.querySelectorAll('path').length).toBe(ICON_PATHS[name].length);
      unmount();
    }
  });

  it('导航/状态常用图标齐备', () => {
    const required: IconName[] = [
      'chevron-down',
      'chevron-up',
      'chevron-left',
      'chevron-right',
      'close',
      'plus',
      'search',
      'check',
      'info',
      'warning',
      'error',
      'sparkle',
      'user',
      'agent',
      'more-horizontal',
      'inbox',
      'settings',
      'home',
      'board',
      'chat',
    ];
    for (const name of required) {
      expect(ICON_PATHS[name], `${name} 存在`).toBeDefined();
    }
  });
});
