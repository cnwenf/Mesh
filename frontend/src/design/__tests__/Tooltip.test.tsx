import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Tooltip } from '../components/Tooltip';

describe('Tooltip(§9.1 hover 与 focus 等价呈现)', () => {
  it('role=tooltip 经 aria-describedby 关联触发元素', () => {
    render(
      <Tooltip content="复制链接">
        <button type="button">复制</button>
      </Tooltip>,
    );
    const tooltip = screen.getByRole('tooltip', { name: '复制链接' });
    const button = screen.getByRole('button', { name: '复制' });
    expect(button).toHaveAttribute('aria-describedby', tooltip.id);
  });

  it('默认隐藏(CSS opacity/visibility 由类控制),内容在 DOM 中', () => {
    render(
      <Tooltip content="提示">
        <span>触发</span>
      </Tooltip>,
    );
    expect(screen.getByRole('tooltip')).toHaveClass('mesh-tooltip');
    expect(screen.getByText('触发').parentElement).toHaveClass('mesh-tooltip-anchor');
  });

  it('非元素子节点时 aria-describedby 挂在锚点容器', () => {
    render(<Tooltip content="纯文本触发">一段文字</Tooltip>);
    // 纯文本子节点时,锚点 span 即文本所在元素
    const anchor = screen.getByText('一段文字');
    expect(anchor).toHaveClass('mesh-tooltip-anchor');
    expect(anchor).toHaveAttribute('aria-describedby', screen.getByRole('tooltip').id);
  });

  it('className 透传到锚点', () => {
    render(
      <Tooltip content="t" className="custom">
        <span>x</span>
      </Tooltip>,
    );
    expect(screen.getByText('x').parentElement).toHaveClass('mesh-tooltip-anchor', 'custom');
  });
});
