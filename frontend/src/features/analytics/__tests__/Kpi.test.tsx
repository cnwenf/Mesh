/**
 * Kpi / KpiStrip 组件测试(design-quality.md §3.2 / §6.3):
 * 大数字 tabular-nums(经既有 .mesh-tnum 工具类)、title 字阶、口径 hint、
 * 语义 tone(颜色非唯一信号——label/hint 文本始终在)、窄屏响应式网格类。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Kpi, KpiValue } from '../Kpi';
import { KpiStrip } from '../KpiStrip';

describe('Kpi', () => {
  it('renders label, big value and hint with tabular nums by default', () => {
    const { container } = render(
      <Kpi label="Created" value={42} hint="Last 30 days" />,
    );
    expect(screen.getByText('Created')).toBeInTheDocument();
    const value = container.querySelector('.mesh-analytics__kpi-big');
    expect(value).not.toBeNull();
    expect(value?.textContent).toContain('42');
    // 数字默认等宽位:叠加排版体系 .mesh-tnum(typography.css §6.3)
    expect(value?.classList.contains('mesh-tnum')).toBe(true);
    expect(screen.getByText('Last 30 days')).toBeInTheDocument();
  });

  it('appends the unit in small text beside the value', () => {
    const { container } = render(<Kpi label="P50" value="2d 0h" unit="each" tabular={false} />);
    const unit = container.querySelector('.mesh-analytics__kpi-unit');
    expect(unit?.textContent).toBe('each');
    // tabular={false} → 不叠加 .mesh-tnum
    const value = container.querySelector('.mesh-analytics__kpi-big');
    expect(value?.classList.contains('mesh-tnum')).toBe(false);
  });

  it('applies tone modifier classes for semantic emphasis', () => {
    const { container } = render(<Kpi label="Net" value={-3} tone="warning" />);
    expect(container.querySelector('.mesh-analytics__kpi-big--warning')).not.toBeNull();
    const danger = render(<Kpi label="x" value={1} tone="danger" />);
    expect(danger.container.querySelector('.mesh-analytics__kpi-big--danger')).not.toBeNull();
    const success = render(<Kpi label="x" value={1} tone="success" />);
    expect(success.container.querySelector('.mesh-analytics__kpi-big--success')).not.toBeNull();
  });

  it('omits hint and unit when empty or absent', () => {
    const { container } = render(<Kpi label="Agents" value={7} unit="" hint="" />);
    expect(container.querySelector('.mesh-analytics__kpi-hint')).toBeNull();
    expect(container.querySelector('.mesh-analytics__kpi-unit')).toBeNull();
  });
});

describe('KpiValue (embedded fragment)', () => {
  it('renders without tone class for default tone', () => {
    const { container } = render(
      <KpiValue value={1} tone="default" tabular={true} />,
    );
    const value = container.querySelector('.mesh-analytics__kpi-big');
    expect(value?.className).not.toContain('--');
  });
});

describe('KpiStrip', () => {
  it('lays children out in the responsive strip grid', () => {
    const { container } = render(
      <KpiStrip label="Window summary">
        <Kpi label="A" value={1} />
        <Kpi label="B" value={2} />
      </KpiStrip>,
    );
    const strip = container.querySelector('.mesh-analytics__kpi-strip');
    expect(strip).not.toBeNull();
    expect(strip?.querySelectorAll('.mesh-analytics__kpi-cell')).toHaveLength(2);
    // 有 label → role=group,读屏可名
    expect(screen.getByRole('group', { name: 'Window summary' })).toBeInTheDocument();
  });

  it('renders without a group role when unlabeled', () => {
    const { container } = render(
      <KpiStrip>
        <Kpi label="A" value={1} />
      </KpiStrip>,
    );
    expect(container.querySelector('[role="group"]')).toBeNull();
  });
});
