import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Dialog } from '../components/Dialog';

describe('Dialog(role=dialog / aria-modal / 焦点圈养 / Esc / 焦点归还)', () => {
  it('open=false 时不渲染', () => {
    render(
      <Dialog open={false} onClose={() => undefined} title="Hidden">
        body
      </Dialog>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('role=dialog + aria-modal,且由 title prop 标注(可访问名)', () => {
    render(
      <Dialog open onClose={() => undefined} title="Confirm action">
        body
      </Dialog>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Confirm action' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('打开后焦点移入 dialog', () => {
    render(
      <Dialog open onClose={() => undefined} title="T">
        body
      </Dialog>,
    );
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('Tab 在末尾折返到首个可聚焦元素(焦点圈养)', async () => {
    const user = userEvent.setup();
    render(
      <Dialog open onClose={() => undefined} title="T">
        <button type="button">First</button>
        <button type="button">Second</button>
      </Dialog>,
    );
    expect(screen.getByRole('dialog')).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Second' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus();
  });

  it('Shift+Tab 在首部/容器上折返到末尾元素', async () => {
    const user = userEvent.setup();
    render(
      <Dialog open onClose={() => undefined} title="T">
        <button type="button">First</button>
        <button type="button">Second</button>
      </Dialog>,
    );
    const first = screen.getByRole('button', { name: 'First' });
    const second = screen.getByRole('button', { name: 'Second' });
    // 焦点在 dialog 容器(打开即聚焦)→ Shift+Tab 折返到末尾
    expect(screen.getByRole('dialog')).toHaveFocus();
    await user.tab({ shift: true });
    expect(second).toHaveFocus();
    // 焦点在首个可聚焦元素 → Shift+Tab 折返到末尾
    first.focus();
    expect(first).toHaveFocus();
    await user.tab({ shift: true });
    expect(second).toHaveFocus();
  });

  it('无可聚焦内容时 Tab 仍把焦点留在 dialog', async () => {
    const user = userEvent.setup();
    render(
      <Dialog open onClose={() => undefined} title="T">
        plain text only
      </Dialog>,
    );
    await user.tab();
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('Esc 关闭(onClose)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <Dialog open onClose={onClose} title="T">
        <button type="button">Inside</button>
      </Dialog>,
    );
    await user.tab();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closeLabel 提供时渲染关闭按钮(鼠标等价路径)', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <Dialog open onClose={onClose} title="T" closeLabel="Close dialog">
        body
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('无 closeLabel 时不渲染关闭按钮', () => {
    render(
      <Dialog open onClose={() => undefined} title="T">
        body
      </Dialog>,
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('点击遮罩关闭;点击对话框内部不关闭', () => {
    const onClose = vi.fn();
    const { container } = render(
      <Dialog open onClose={onClose} title="T">
        <p>inner</p>
      </Dialog>,
    );
    const backdrop = container.querySelector('.mesh-dialog__backdrop');
    expect(backdrop).not.toBeNull();
    const inner = screen.getByText('inner');
    fireEvent.mouseDown(inner);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(backdrop as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('关闭后焦点归还触发元素', async () => {
    const user = userEvent.setup();
    function Harness(): React.JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" onClick={() => setOpen(true)}>
            Open dialog
          </button>
          <Dialog open={open} onClose={() => setOpen(false)} title="T" closeLabel="Close">
            <p>content</p>
          </Dialog>
        </div>
      );
    }
    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    await user.click(trigger);
    expect(screen.getByRole('dialog')).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
