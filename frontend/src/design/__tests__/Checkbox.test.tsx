/**
 * Checkbox 契约测试(design-quality §7.4/§9.1)。
 */
import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Checkbox } from '../components/Checkbox';

describe('Checkbox(自绘盒体 + 原生语义)', () => {
  it('label 关联原生 checkbox,点击切换', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Checkbox label="订阅通知" onChange={onChange} />);
    const box = screen.getByLabelText('订阅通知');
    expect(box).toHaveAttribute('type', 'checkbox');
    await user.click(box);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('description 与 error 经 describedby 关联;error 置 aria-invalid', () => {
    render(<Checkbox label="选项" description="说明文字" error="必选" />);
    const box = screen.getByLabelText('选项');
    expect(box).toHaveAttribute('aria-invalid', 'true');
    const ids = (box.getAttribute('aria-describedby') ?? '').split(' ');
    expect(ids).toHaveLength(2);
    expect(document.getElementById(ids[0] ?? '')).toHaveTextContent('说明文字');
    expect(document.getElementById(ids[1] ?? '')).toHaveTextContent('必选');
  });

  it('仅 description 时只关联一项', () => {
    render(<Checkbox label="选项" description="说明文字" />);
    const box = screen.getByLabelText('选项');
    const describedBy = box.getAttribute('aria-describedby') ?? '';
    expect(describedBy.split(' ')).toHaveLength(1);
    expect(box).not.toHaveAttribute('aria-invalid');
  });

  it('indeterminate 同步到原生属性并渲染横杠标记', () => {
    const { container, rerender } = render(<Checkbox label="全选" indeterminate />);
    const box = screen.getByLabelText('全选') as HTMLInputElement;
    expect(box.indeterminate).toBe(true);
    expect(container.querySelector('.mesh-checkbox__minus')).not.toBeNull();
    rerender(<Checkbox label="全选" indeterminate={false} />);
    expect(box.indeterminate).toBe(false);
    expect(container.querySelector('.mesh-checkbox__minus')).toBeNull();
  });

  it('ref 转发(对象 ref 与回调 ref)', () => {
    const objectRef = createRef<HTMLInputElement>();
    const holder: { node: HTMLInputElement | null } = { node: null };
    render(<Checkbox label="对象" ref={objectRef} />);
    expect(objectRef.current?.type).toBe('checkbox');
    render(
      <Checkbox
        label="回调"
        ref={(node) => {
          holder.node = node;
        }}
      />,
    );
    expect(holder.node?.type).toBe('checkbox');
  });
});
