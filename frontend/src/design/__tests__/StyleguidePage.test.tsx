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
});
