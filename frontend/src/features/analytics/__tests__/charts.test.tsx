/**
 * charts.tsx 渲染测试(analytics.md §4.5):语义 token 取色、线型区分、
 * 文本兜底(<title>/aria-label)、空序列兜底。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { GroupedBarChart, LineChart, Sparkline } from '../charts';

describe('LineChart', () => {
  it('renders one path per series with dashed style for the ideal line', () => {
    render(
      <LineChart
        ariaLabel="burndown"
        xLabels={['07-06', '07-07', '07-08']}
        series={[
          {
            name: 'Ideal',
            colorToken: 'neutral',
            dashed: true,
            points: [
              { x: 0, y: 6 },
              { x: 1, y: 5 },
              { x: 2, y: 4 },
            ],
          },
          {
            name: 'Actual',
            colorToken: 'info',
            points: [
              { x: 0, y: 6 },
              { x: 1, y: 6 },
              { x: 2, y: 3 },
            ],
          },
        ]}
      />,
    );
    const chart = screen.getByTestId('analytics-line-chart');
    expect(chart).toBeInTheDocument();
    const ideal = screen.getByTestId('analytics-line-Ideal');
    expect(ideal.getAttribute('stroke-dasharray')).toBe('6 4');
    expect(ideal.getAttribute('stroke')).toBe('var(--color-text-muted)');
    const actual = screen.getByTestId('analytics-line-Actual');
    expect(actual.getAttribute('stroke-dasharray')).toBeNull();
    expect(actual.getAttribute('stroke')).toBe('var(--color-info)');
  });

  it('handles a single x label without dividing by zero', () => {
    render(
      <LineChart
        ariaLabel="single"
        xLabels={['only']}
        series={[{ name: 'S', colorToken: 'success', points: [{ x: 0, y: 2 }] }]}
      />,
    );
    expect(screen.getByTestId('analytics-line-S')).toBeInTheDocument();
  });

  it('uses explicit yMax when provided', () => {
    render(
      <LineChart
        ariaLabel="capped"
        xLabels={['a', 'b']}
        yMax={100}
        series={[
          {
            name: 'S',
            colorToken: 'danger',
            points: [
              { x: 0, y: 1 },
              { x: 1, y: 2 },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByTestId('analytics-line-S')).toBeInTheDocument();
  });

  it('samples x labels for dense series', () => {
    const labels = Array.from({ length: 13 }, (_, i) => `d${i}`);
    render(
      <LineChart
        ariaLabel="dense"
        xLabels={labels}
        series={[
          { name: 'S', colorToken: 'info', points: labels.map((_, i) => ({ x: i, y: i })) },
        ]}
      />,
    );
    // 13 labels, step = ceil(13/6) = 3 → visible: d0, d3, d6, d9, d12
    expect(screen.getByText('d0')).toBeInTheDocument();
    expect(screen.getByText('d12')).toBeInTheDocument();
    expect(screen.queryByText('d1')).toBeNull();
  });
});

describe('GroupedBarChart', () => {
  it('renders bars per group with titles and semantic fills', () => {
    render(
      <GroupedBarChart
        ariaLabel="velocity"
        groups={[
          {
            label: 'C1',
            bars: [
              { name: 'Issues', value: 4, colorToken: 'info' },
              { name: 'Points', value: 9, colorToken: 'success' },
            ],
          },
          {
            label: 'C2',
            bars: [{ name: 'Issues', value: 0, colorToken: 'info' }],
          },
        ]}
      />,
    );
    expect(screen.getByTestId('analytics-bar-chart')).toBeInTheDocument();
    const bars = screen.getAllByTestId('analytics-bar-Issues');
    expect(bars).toHaveLength(2);
    expect(screen.getAllByTestId('analytics-bar-Points')[0].getAttribute('fill')).toBe(
      'var(--color-success)',
    );
  });

  it('handles an empty group list', () => {
    render(<GroupedBarChart ariaLabel="empty" groups={[]} />);
    expect(screen.getByTestId('analytics-bar-chart')).toBeInTheDocument();
  });
});

describe('Sparkline', () => {
  it('renders a path for values and an empty svg otherwise', () => {
    const { rerender } = render(
      <Sparkline ariaLabel="trend" colorToken="info" values={[1, 3, 2, 5]} />,
    );
    const svg = screen.getByTestId('analytics-sparkline');
    expect(svg.querySelector('path')).not.toBeNull();
    rerender(<Sparkline ariaLabel="trend" colorToken="info" values={[]} />);
    expect(screen.getByTestId('analytics-sparkline').querySelector('path')).toBeNull();
  });

  it('handles a single value', () => {
    render(<Sparkline ariaLabel="one" colorToken="success" values={[4]} />);
    expect(screen.getByTestId('analytics-sparkline').querySelector('path')).not.toBeNull();
  });
});
