import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Accordion } from '../components/Accordion';
import type { AccordionItem } from '../components/Accordion';

const ITEMS: AccordionItem[] = [
  { value: 'trigger', title: '触发条件', content: <p>定时或事件</p> },
  { value: 'guard', title: '护栏', content: <p>预算与权限</p> },
];

describe('Accordion(§1.2 渐进披露)', () => {
  it('初始全部折叠,trigger 带 aria-expanded=false', () => {
    render(<Accordion items={ITEMS} />);
    for (const title of ['触发条件', '护栏']) {
      expect(screen.getByRole('button', { name: title })).toHaveAttribute('aria-expanded', 'false');
    }
    expect(screen.queryByRole('region')).toBeNull();
  });

  it('点击展开:aria-expanded=true,panel role=region 且 aria-labelledby 关联', async () => {
    const user = userEvent.setup();
    render(<Accordion items={ITEMS} />);
    const trigger = screen.getByRole('button', { name: '触发条件' });
    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const panel = screen.getByRole('region');
    expect(panel).toHaveAttribute('aria-labelledby', trigger.id);
    expect(panel).toHaveTextContent('定时或事件');
  });

  it('单选模式:展开另一项自动收起前一项', async () => {
    const user = userEvent.setup();
    render(<Accordion items={ITEMS} />);
    await user.click(screen.getByRole('button', { name: '触发条件' }));
    await user.click(screen.getByRole('button', { name: '护栏' }));
    expect(screen.getByRole('button', { name: '触发条件' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: '护栏' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getAllByRole('region')).toHaveLength(1);
  });

  it('multiple 模式允许同时展开', async () => {
    const user = userEvent.setup();
    render(<Accordion items={ITEMS} multiple />);
    await user.click(screen.getByRole('button', { name: '触发条件' }));
    await user.click(screen.getByRole('button', { name: '护栏' }));
    expect(screen.getAllByRole('region')).toHaveLength(2);
  });

  it('再次点击已展开项则收起(单选模式)', async () => {
    const user = userEvent.setup();
    render(<Accordion items={ITEMS} />);
    const trigger = screen.getByRole('button', { name: '触发条件' });
    await user.click(trigger);
    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('defaultExpanded 非受控初始展开;onExpandedChange 回调展开集', async () => {
    const user = userEvent.setup();
    const onExpandedChange = vi.fn();
    render(<Accordion items={ITEMS} defaultExpanded={['guard']} onExpandedChange={onExpandedChange} />);
    expect(screen.getByRole('button', { name: '护栏' })).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('button', { name: '触发条件' }));
    expect(onExpandedChange).toHaveBeenCalledWith(['trigger']);
  });

  it('受控:expanded 不变则 UI 不变,回调仍发出', async () => {
    const user = userEvent.setup();
    const onExpandedChange = vi.fn();
    render(<Accordion items={ITEMS} expanded={[]} onExpandedChange={onExpandedChange} />);
    await user.click(screen.getByRole('button', { name: '触发条件' }));
    expect(onExpandedChange).toHaveBeenCalledWith(['trigger']);
    expect(screen.queryByRole('region')).toBeNull();
  });

  it('className 透传', () => {
    const { container } = render(<Accordion items={ITEMS} className="custom" />);
    expect(container.querySelector('.mesh-accordion')).toHaveClass('custom');
  });
});
