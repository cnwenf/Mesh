import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Tabs } from '../components/Tabs';
import type { TabItem } from '../components/Tabs';

const ITEMS: TabItem[] = [
  { value: 'overview', label: '概览', content: <div>概览内容</div>, testId: 'overview-tab' },
  { value: 'issues', label: '工作项', content: <div>工作项内容</div> },
  { value: 'settings', label: '设置', content: <div>设置内容</div>, disabled: true },
];

describe('Tabs(ARIA tabs + 漫游 tabindex + 方向键)', () => {
  it('非受控默认选中首个可用项,渲染对应 panel', () => {
    render(<Tabs items={ITEMS} label="对象页签" />);
    expect(screen.getByRole('tablist', { name: '对象页签' })).toHaveAttribute(
      'data-slot',
      'tabs-list',
    );
    const overview = screen.getByRole('tab', { name: '概览' });
    expect(overview).toHaveAttribute('data-testid', 'overview-tab');
    expect(overview).toHaveAttribute('aria-selected', 'true');
    expect(overview).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: '工作项' })).toHaveAttribute('tabindex', '-1');
    expect(screen.getByText('概览内容')).toBeInTheDocument();
    expect(screen.queryByText('工作项内容')).toBeNull();
    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveAttribute('aria-labelledby', overview.id);
    expect(overview).toHaveAttribute('aria-controls', panel.id);
  });

  it('defaultValue 指定初始页签', () => {
    render(<Tabs items={ITEMS} label="t" defaultValue="issues" />);
    expect(screen.getByRole('tab', { name: '工作项' })).toHaveAttribute('aria-selected', 'true');
  });

  it('点击切换并回调 onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Tabs items={ITEMS} label="t" onChange={onChange} />);
    await user.click(screen.getByRole('tab', { name: '工作项' }));
    expect(onChange).toHaveBeenCalledWith('issues');
    expect(screen.getByText('工作项内容')).toBeInTheDocument();
  });

  it('受控:value 不变则不切换,onChange 仍被调用', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Tabs items={ITEMS} label="t" value="overview" onChange={onChange} />);
    await user.click(screen.getByRole('tab', { name: '工作项' }));
    expect(onChange).toHaveBeenCalledWith('issues');
    expect(screen.getByText('概览内容')).toBeInTheDocument();
  });

  it('ArrowRight/ArrowLeft 切换选中与焦点(跳过禁用项)', () => {
    const onChange = vi.fn();
    render(<Tabs items={ITEMS} label="t" onChange={onChange} />);
    const overview = screen.getByRole('tab', { name: '概览' });
    overview.focus();
    fireEvent.keyDown(overview, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '工作项' }));
    expect(screen.getByRole('tab', { name: '工作项' })).toHaveAttribute('aria-selected', 'true');
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith('issues');
    // 「设置」禁用 → 循环回「概览」
    fireEvent.keyDown(screen.getByRole('tab', { name: '工作项' }), { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '概览' }));
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith('overview');
    // ← 反向同样跳过禁用
    fireEvent.keyDown(screen.getByRole('tab', { name: '概览' }), { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '工作项' }));
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(onChange).toHaveBeenLastCalledWith('issues');
  });

  it('Home/End 跳首末可用页签', async () => {
    const user = userEvent.setup();
    render(<Tabs items={ITEMS} label="t" />);
    const overview = screen.getByRole('tab', { name: '概览' });
    overview.focus();
    fireEvent.keyDown(overview, { key: 'End' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '工作项' }));
    fireEvent.keyDown(screen.getByRole('tab', { name: '工作项' }), { key: 'Home' });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '概览' }));
  });

  it('无关按键不改变选中', () => {
    render(<Tabs items={ITEMS} label="t" />);
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'Enter' });
    expect(screen.getByRole('tab', { name: '概览' })).toHaveAttribute('aria-selected', 'true');
  });

  it('全部禁用时不崩溃且无 panel', () => {
    render(<Tabs items={[{ value: 'x', label: 'X', content: null, disabled: true }]} label="t" />);
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' });
    expect(screen.queryByRole('tabpanel')).toBeNull();
  });

  it('className 透传', () => {
    const { container } = render(<Tabs items={ITEMS} label="t" className="custom" />);
    expect(container.querySelector('.mesh-tabs')).toHaveClass('custom');
    expect(container.querySelector('[data-slot="tabs"]')).not.toBeNull();
  });
});

describe('Tabs 焦点兜底(验收 R1-M3)', () => {
  const ITEMS = [
    { value: 'a', label: '甲', content: '甲内容' },
    { value: 'b', label: '乙', content: '乙内容' },
  ];

  it('受控 value 未命中任何项时,首个可用项可聚焦且呈现其面板(杜绝整组 tabIndex=-1)', () => {
    render(<Tabs label="兜底" value="ghost" onChange={() => undefined} items={ITEMS} />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.filter((tab) => tab.getAttribute('tabindex') === '0')).toHaveLength(1);
    expect(screen.getByRole('tab', { name: '甲' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('甲内容')).toBeInTheDocument();
  });

  it('受控 value 命中禁用项时同样回退首个可用项', () => {
    render(
      <Tabs
        label="兜底禁用"
        value="b"
        onChange={() => undefined}
        items={[
          { value: 'a', label: '甲', content: '甲内容' },
          { value: 'b', label: '乙', content: '乙内容', disabled: true },
        ]}
      />,
    );
    expect(screen.getByRole('tab', { name: '甲' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: '甲' })).toHaveAttribute('aria-selected', 'true');
  });
});
