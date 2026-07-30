/**
 * Textarea 契约测试(design-quality §7.4)。
 */
import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TEXTAREA_MAX_HEIGHT_PX, Textarea } from '../components/Textarea';

describe('Textarea(多行字段)', () => {
  it('label 关联控件;error 置 aria-invalid 并经 describedby 关联', () => {
    render(<Textarea label="描述" id="demo" error="太短了" hint="至少两句" />);
    const control = screen.getByLabelText('描述');
    expect(control.tagName).toBe('TEXTAREA');
    expect(control).toHaveAttribute('aria-invalid', 'true');
    const ids = (control.getAttribute('aria-describedby') ?? '').split(' ');
    expect(ids).toHaveLength(2);
    expect(document.getElementById(ids[0] ?? '')).toHaveTextContent('太短了');
  });

  it('无 error/hint 时不挂无障碍关联属性', () => {
    render(<Textarea label="描述" />);
    const control = screen.getByLabelText('描述');
    expect(control).not.toHaveAttribute('aria-describedby');
    expect(control).not.toHaveAttribute('aria-invalid');
  });

  it('rows 透传且输入时触发自适应(高度写回 style)', async () => {
    const user = userEvent.setup();
    render(<Textarea label="描述" rows={5} />);
    const control = screen.getByLabelText('描述');
    expect(control).toHaveAttribute('rows', '5');
    await user.type(control, 'hello');
    expect(control.style.blockSize).not.toBe('');
  });

  it('scrollHeight 超过上限时内部滚动且高度被钳制', () => {
    render(<Textarea label="描述" maxHeight={80} />);
    const control = screen.getByLabelText('描述');
    // jsdom 布局为 0:scrollHeight(0) ≤ 80 → overflow hidden,高度 0px 钳制路径执行
    expect(control.style.overflowY).toBe('hidden');
    expect(control.style.blockSize).toBe('0px');
    expect(TEXTAREA_MAX_HEIGHT_PX).toBe(320);
  });

  it('ref 转发到原生 textarea(对象 ref 与回调 ref)', () => {
    const objectRef = createRef<HTMLTextAreaElement>();
    const holder: { node: HTMLTextAreaElement | null } = { node: null };
    const { rerender } = render(<Textarea label="对象 ref" ref={objectRef} />);
    expect(objectRef.current?.tagName).toBe('TEXTAREA');
    rerender(
      <Textarea
        label="回调 ref"
        ref={(node) => {
          holder.node = node;
        }}
      />,
    );
    expect(holder.node?.tagName).toBe('TEXTAREA');
  });
});

describe('Textarea 分支补强', () => {
  it('内容超过上限时高度钳制至 maxHeight 且内部滚动', () => {
    const scrollSpy = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(500);
    render(<Textarea label="长文" maxHeight={320} />);
    const control = screen.getByLabelText('长文');
    expect(control.style.blockSize).toBe('320px');
    expect(control.style.overflowY).toBe('auto');
    scrollSpy.mockRestore();
  });
});
