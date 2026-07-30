import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Button, buttonClasses } from '../components/Button';

describe('Button', () => {
  it('渲染子内容并响应点击', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<Button onClick={onClick}>Save</Button>);
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('默认 type=button(避免意外表单提交)、variant=primary、size=md', () => {
    render(<Button>Go</Button>);
    const button = screen.getByRole('button', { name: 'Go' });
    expect(button).toHaveAttribute('type', 'button');
    expect(button.className).toContain('mesh-button--primary');
    expect(button.className).toContain('mesh-button--md');
  });

  it.each(['primary', 'secondary', 'ghost', 'danger'] as const)('variant=%s 落到类名', (variant) => {
    render(<Button variant={variant}>V</Button>);
    expect(screen.getByRole('button', { name: 'V' }).className).toContain(
      `mesh-button--${variant}`,
    );
  });

  it('size=sm 落到类名', () => {
    render(<Button size="sm">S</Button>);
    expect(screen.getByRole('button', { name: 'S' }).className).toContain('mesh-button--sm');
  });

  it('合并外部 className', () => {
    render(<Button className="custom">C</Button>);
    const button = screen.getByRole('button', { name: 'C' });
    expect(button.className).toContain('mesh-button');
    expect(button.className).toContain('custom');
  });

  it('isLoading:禁用 + aria-busy + 保持可访问名 + 不触发点击', () => {
    const onClick = vi.fn();
    render(
      <Button isLoading onClick={onClick}>
        Submit
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Submit' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('isLoading 渲染 aria-hidden 的 spinner', () => {
    const { container } = render(<Button isLoading>Loading</Button>);
    const spinner = container.querySelector('.mesh-button__spinner');
    expect(spinner).not.toBeNull();
    expect(spinner).toHaveAttribute('aria-hidden', 'true');
  });

  it('disabled 透传且不点击', () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        D
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'D' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('键盘可达:Tab 聚焦,Enter/Space 激活(原生按钮语义)', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<Button onClick={onClick}>KB</Button>);
    await user.tab();
    expect(screen.getByRole('button', { name: 'KB' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(onClick).toHaveBeenCalledTimes(1);
    await user.keyboard(' ');
    expect(onClick).toHaveBeenCalledTimes(2);
  });

  it('转发 ref 到底层 button 元素', () => {
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref}>R</Button>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
    expect(ref.current?.textContent).toContain('R');
  });
});

describe('Button(design-quality.md §7.3 尺寸与状态矩阵)', () => {
  it('默认 md;lg 触控档类名正确', () => {
    const { rerender } = render(
      <Button size="lg" isLoading>
        提交
      </Button>,
    );
    const button = screen.getByRole('button', { name: '提交' });
    expect(button).toHaveClass('mesh-button--lg');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    rerender(<Button size="sm">小</Button>);
    expect(screen.getByRole('button', { name: '小' })).toHaveClass('mesh-button--sm');
  });

  it('buttonClasses 组合 variant/size/className,空值过滤', () => {
    expect(buttonClasses('danger', 'lg')).toBe('mesh-button mesh-button--danger mesh-button--lg');
    expect(buttonClasses('ghost', 'md', 'extra')).toBe(
      'mesh-button mesh-button--ghost mesh-button--md extra',
    );
  });
});
