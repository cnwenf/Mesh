/**
 * StyleguidePage — 组件状态 fixture 页冒烟(design-quality §12 Phase 1 视觉回归基础)。
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { StyleguidePage } from '../StyleguidePage';

describe('StyleguidePage(组件状态 fixture)', () => {
  it('全部状态分区在场且主标题唯一', () => {
    renderWithProviders(<StyleguidePage />);
    for (const section of ['按钮', '图标', '表单', '徽标与头像', '标签页', '反馈与状态', '页头 / 工具条 / 表格', '排版']) {
      expect(screen.getByTestId('styleguide-' + section)).toBeInTheDocument();
    }
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });

  it('图标注册表全量呈现(无静默缺图标)', () => {
    renderWithProviders(<StyleguidePage />);
    const section = screen.getByTestId('styleguide-图标');
    expect(section.querySelectorAll('svg').length).toBeGreaterThanOrEqual(50);
  });

  it('状态矩阵样本在场:禁用/loading 按钮、错误字段、空态/错误态四部件', () => {
    renderWithProviders(<StyleguidePage />);
    expect(screen.getByRole('button', { name: '禁用' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '提交中' })).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByLabelText('邮箱')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('还没有收件')).toBeInTheDocument();
    expect(screen.getByText('加载失败')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.getByText('diag-0001')).toBeInTheDocument();
  });

  it('表格排序可交互(点击表头上抛并翻转方向)', async () => {
    const user = userEvent.setup();
    renderWithProviders(<StyleguidePage />);
    const header = screen.getByRole('columnheader', { name: /编号/ });
    expect(header).toHaveAttribute('aria-sort', 'ascending');
    await user.click(screen.getByRole('button', { name: '编号' }));
    expect(header).toHaveAttribute('aria-sort', 'descending');
  });

  it('Tabs 与 Switch 交互生效(fixture 自身状态可操作)', async () => {
    const user = userEvent.setup();
    renderWithProviders(<StyleguidePage />);
    await user.click(screen.getByRole('tab', { name: '动态' }));
    expect(screen.getByRole('tab', { name: '动态' })).toHaveAttribute('aria-selected', 'true');
    const switchControl = screen.getByRole('switch', { name: /仅显示我的/ });
    expect(switchControl).toHaveAttribute('aria-checked', 'true');
    await user.click(switchControl);
    expect(switchControl).toHaveAttribute('aria-checked', 'false');
  });

  it('浮层与反馈交互生效(Menu/Popover 开合、重试、半选切换)', async () => {
    const user = userEvent.setup();
    renderWithProviders(<StyleguidePage />);
    // Menu:触发器打开 → 执行「编辑」项(onSelect lambda)
    await user.click(screen.getByRole('button', { name: '行操作' }));
    await user.click(screen.getByRole('menuitem', { name: '编辑' }));
    expect(screen.queryByRole('menu')).toBeNull();
    // 危险项同样可执行(danger 分支)
    await user.click(screen.getByRole('button', { name: '行操作' }));
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    // Popover:触发器打开 role=dialog 浮层(与工具条「筛选」按钮按 haspopup 区分)
    const popoverTrigger = screen
      .getAllByRole('button', { name: '筛选' })
      .find((button) => button.getAttribute('aria-haspopup') === 'dialog');
    if (popoverTrigger === undefined) throw new Error('缺少 Popover 触发器');
    await user.click(popoverTrigger);
    expect(screen.getByRole('dialog', { name: '筛选面板' })).toBeInTheDocument();
    // ErrorState 重试(onRetry lambda)
    await user.click(screen.getByRole('button', { name: '重试' }));
    // 半选 Checkbox 切换(onChange lambda)
    await user.click(screen.getByLabelText('半选父项'));
  });
});
