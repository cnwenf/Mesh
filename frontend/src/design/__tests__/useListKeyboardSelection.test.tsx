import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { useListKeyboardSelection } from '../patterns/useListKeyboardSelection';

interface HarnessProps {
  readonly count: number;
  readonly onOpen?: (index: number) => void;
  readonly onToggle?: (index: number) => void;
}

/** 行容器测试支架:登记 data-list-item-index 与 roving tabindex。 */
function Harness({ count, onOpen, onToggle }: HarnessProps): React.JSX.Element {
  const [countOverride] = useState(count);
  const selection = useListKeyboardSelection({ itemCount: countOverride, onOpen, onToggle });
  return (
    <div ref={selection.containerRef} data-testid="list">
      {Array.from({ length: countOverride }, (_, index) => (
        <div
          key={index}
          role="option"
          aria-selected={selection.activeIndex === index}
          data-list-item-index={index}
          tabIndex={selection.itemTabIndex(index)}
          onKeyDown={(event) => selection.handleItemKeyDown(event, index)}
          onClick={() => selection.activate(index)}
        >
          {`行${index}`}
        </div>
      ))}
      <span data-testid="active">{selection.activeIndex}</span>
    </div>
  );
}

describe('useListKeyboardSelection(DataView 键盘行选择,§3.2/§10.2)', () => {
  it('初始无活动行:首行 tabIndex=0,其余 -1', () => {
    render(<Harness count={3} />);
    const rows = screen.getAllByRole('option');
    expect(rows[0]).toHaveAttribute('tabindex', '0');
    expect(rows[1]).toHaveAttribute('tabindex', '-1');
    expect(screen.getByTestId('active')).toHaveTextContent('-1');
  });

  it('ArrowDown/ArrowUp 移动活动行并真实移焦', async () => {
    const user = userEvent.setup();
    render(<Harness count={3} />);
    const rows = screen.getAllByRole('option');
    rows[0].focus();
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(rows[1]);
    expect(screen.getByTestId('active')).toHaveTextContent('1');
    expect(rows[1]).toHaveAttribute('tabindex', '0');
    expect(rows[0]).toHaveAttribute('tabindex', '-1');
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(rows[2]);
    // 末行不越界
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(rows[2]);
    await user.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(rows[1]);
    // 首行不越界
    await user.keyboard('{ArrowUp}');
    await user.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(rows[0]);
  });

  it('ArrowRight/ArrowLeft 同样生效(横向列表复用)', async () => {
    const user = userEvent.setup();
    render(<Harness count={2} />);
    screen.getAllByRole('option')[0].focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByTestId('active')).toHaveTextContent('1');
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByTestId('active')).toHaveTextContent('0');
  });

  it('Home/End 跳首尾', async () => {
    const user = userEvent.setup();
    render(<Harness count={5} />);
    screen.getAllByRole('option')[0].focus();
    await user.keyboard('{End}');
    expect(screen.getByTestId('active')).toHaveTextContent('4');
    await user.keyboard('{Home}');
    expect(screen.getByTestId('active')).toHaveTextContent('0');
  });

  it('Enter 打开当前行(行主操作)', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<Harness count={3} onOpen={onOpen} />);
    screen.getAllByRole('option')[0].focus();
    await user.keyboard('{ArrowDown}{Enter}');
    expect(onOpen).toHaveBeenCalledWith(1);
  });

  it('空格切换选中(提供 onToggle 时),未提供时不拦截', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<Harness count={2} onToggle={onToggle} />);
    screen.getAllByRole('option')[0].focus();
    await user.keyboard(' ');
    expect(onToggle).toHaveBeenCalledWith(0);
  });

  it('点击行编程式激活并移焦', async () => {
    const user = userEvent.setup();
    render(<Harness count={3} />);
    const rows = screen.getAllByRole('option');
    await user.click(rows[2]);
    expect(document.activeElement).toBe(rows[2]);
    expect(screen.getByTestId('active')).toHaveTextContent('2');
  });

  it('越界 activate 无效', async () => {
    const user = userEvent.setup();
    function Probe(): React.JSX.Element {
      const selection = useListKeyboardSelection({ itemCount: 2 });
      return (
        <div ref={selection.containerRef}>
          <button type="button" onClick={() => selection.activate(9)}>
            越界
          </button>
          <span data-testid="active">{selection.activeIndex}</span>
        </div>
      );
    }
    render(<Probe />);
    await user.click(screen.getByRole('button', { name: '越界' }));
    expect(screen.getByTestId('active')).toHaveTextContent('-1');
  });

  it('数据收缩后活动行越界回退为 -1', async () => {
    const user = userEvent.setup();
    function Shrink(): React.JSX.Element {
      const [count, setCount] = useState(5);
      const selection = useListKeyboardSelection({ itemCount: count });
      return (
        <div ref={selection.containerRef}>
          {Array.from({ length: count }, (_, index) => (
            <div
              key={index}
              data-list-item-index={index}
              tabIndex={selection.itemTabIndex(index)}
              onKeyDown={(event) => selection.handleItemKeyDown(event, index)}
            />
          ))}
          <button type="button" onClick={() => selection.activate(4)}>
            选末行
          </button>
          <button type="button" onClick={() => setCount(2)}>
            收缩
          </button>
          <span data-testid="active">{selection.activeIndex}</span>
        </div>
      );
    }
    render(<Shrink />);
    await user.click(screen.getByRole('button', { name: '选末行' }));
    expect(screen.getByTestId('active')).toHaveTextContent('4');
    await user.click(screen.getByRole('button', { name: '收缩' }));
    expect(screen.getByTestId('active')).toHaveTextContent('-1');
  });
});
