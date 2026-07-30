/**
 * Popover 契约测试(design-quality §7.5:非模态次级上下文浮层)。
 */
import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Popover } from '../components/Popover';

describe('Popover(次级上下文浮层)', () => {
  it('点击触发器打开 role=dialog 浮层,焦点进入首个可聚焦元素', async () => {
    const user = userEvent.setup();
    render(
      <Popover trigger="filter" triggerLabel="筛选" label="筛选面板">
        <button type="button">首个动作</button>
      </Popover>,
    );
    const trigger = screen.getByRole('button', { name: '筛选' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: '筛选面板' });
    expect(dialog).toBeInTheDocument();
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(trigger.getAttribute('aria-controls')).toBe(dialog.id);
    // 下一帧聚焦首元素
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '首个动作' })).toHaveFocus());
  });

  it('内容无可聚焦元素时聚焦容器自身', async () => {
    const user = userEvent.setup();
    render(
      <Popover trigger="i" triggerLabel="说明" label="说明卡">
        <p>纯文本内容</p>
      </Popover>,
    );
    await user.click(screen.getByRole('button', { name: '说明' }));
    const dialog = screen.getByRole('dialog');
    await vi.waitFor(() => expect(dialog).toHaveFocus());
  });

  it('Esc 关闭并归还焦点触发器;再次点击触发器也关闭', async () => {
    const user = userEvent.setup();
    render(
      <Popover trigger="t" triggerLabel="触发" label="浮层">
        <p>内容</p>
      </Popover>,
    );
    const trigger = screen.getByRole('button', { name: '触发' });
    await user.click(trigger);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(trigger).toHaveFocus();
    // 再开,再点触发器关闭
    await user.click(trigger);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.click(trigger);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it('点外关闭(mousedown)', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button type="button">外部</button>
        <Popover trigger="t" triggerLabel="触发" label="浮层">
          <p>内容</p>
        </Popover>
      </div>,
    );
    await user.click(screen.getByRole('button', { name: '触发' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '外部' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('受控 open + onOpenChange;align=end/width=auto 走分支', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    function Controlled(): React.JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <Popover
          trigger="t"
          triggerLabel="触发"
          label="浮层"
          align="end"
          width="auto"
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            onOpenChange(next);
          }}
        >
          <p>内容</p>
        </Popover>
      );
    }
    render(<Controlled />);
    const trigger = screen.getByRole('button', { name: '触发' });
    await user.click(trigger);
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(screen.queryByRole('dialog')).toBeNull();
    // 受控下焦点归还在 open 翻转后执行
    expect(trigger).toHaveFocus();
  });
});

describe('Popover 分支补强', () => {
  it('下方空间不足且上方更宽裕时翻转向上渲染', async () => {
    const user = userEvent.setup();
    const heightSpy = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(200);
    const widthSpy = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(288);
    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      top: 356,
      bottom: 380,
      left: 10,
      right: 50,
      width: 40,
      height: 24,
      x: 10,
      y: 356,
      toJSON: () => undefined,
    });
    const innerSpy = vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(400);
    try {
      render(
        <Popover trigger="t" triggerLabel="触发" label="翻转浮层">
          <p>内容</p>
        </Popover>,
      );
      await user.click(screen.getByRole('button', { name: '触发' }));
      const dialog = screen.getByRole('dialog');
      expect(dialog.className).toContain('mesh-popover--above');
    } finally {
      heightSpy.mockRestore();
      widthSpy.mockRestore();
      rectSpy.mockRestore();
      innerSpy.mockRestore();
    }
  });
});
