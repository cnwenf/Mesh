/**
 * 统计图表(analytics.md §4.5):手写 SVG,颜色一律经语义 token 引用
 * (var(--color-*)),亮/暗主题随 token 集整体替换;线型(虚/实)与文字
 * 标签保证颜色不作唯一信号;入场无动画(尊重 prefers-reduced-motion)。
 */

export type ChartColorToken = 'success' | 'danger' | 'info' | 'warn' | 'neutral';

/** 语义色 token → CSS 变量(图例色块与图表系列共用同一映射,亮暗自如)。 */
export const TOKEN_VAR: Record<ChartColorToken, string> = {
  success: 'var(--color-success)',
  danger: 'var(--color-danger)',
  info: 'var(--color-info)',
  warn: 'var(--color-warn)',
  neutral: 'var(--color-text-muted)',
};

export interface LineSeries {
  readonly name: string;
  readonly colorToken: ChartColorToken;
  readonly dashed?: boolean;
  readonly points: ReadonlyArray<{ readonly x: number; readonly y: number }>;
}

export interface LineChartProps {
  readonly series: readonly LineSeries[];
  readonly xLabels: readonly string[];
  readonly ariaLabel: string;
  readonly width?: number;
  readonly height?: number;
  /** Y 轴最大值覆盖(缺省取序列最大值,≥1 防零除) */
  readonly yMax?: number;
}

const PAD_LEFT = 36;
const PAD_BOTTOM = 22;
const PAD_TOP = 10;
const PAD_RIGHT = 10;

function niceMax(value: number): number {
  return Math.max(1, Math.ceil(value));
}

/** 多序列折线图:X 为类目序索引,Y 线性;每序列 <title> 提供文本兜底。 */
export function LineChart(props: LineChartProps): React.JSX.Element {
  const { series, xLabels, ariaLabel } = props;
  const width = props.width ?? 560;
  const height = props.height ?? 220;
  const pointCount = Math.max(1, xLabels.length);
  const maxFromSeries = series.reduce(
    (acc, s) => Math.max(acc, ...s.points.map((p) => p.y)),
    0,
  );
  const yMax = props.yMax ?? niceMax(maxFromSeries);
  const innerW = width - PAD_LEFT - PAD_RIGHT;
  const innerH = height - PAD_TOP - PAD_BOTTOM;
  const xFor = (index: number): number =>
    PAD_LEFT + (pointCount <= 1 ? innerW / 2 : (index / (pointCount - 1)) * innerW);
  const yFor = (value: number): number => PAD_TOP + innerH - (value / yMax) * innerH;

  // X 轴标签最多显示 6 个,均匀抽样防重叠
  const labelStep = Math.max(1, Math.ceil(pointCount / 6));

  return (
    <svg
      className="mesh-analytics__chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel}
      data-testid="analytics-line-chart"
    >
      {/* Y 轴刻度(0 / 中 / 顶) */}
      {[0, 0.5, 1].map((frac) => (
        <g key={frac}>
          <line
            x1={PAD_LEFT}
            x2={width - PAD_RIGHT}
            y1={PAD_TOP + innerH - frac * innerH}
            y2={PAD_TOP + innerH - frac * innerH}
            className="mesh-analytics__grid"
          />
          <text
            x={PAD_LEFT - 6}
            y={PAD_TOP + innerH - frac * innerH + 4}
            textAnchor="end"
            className="mesh-analytics__tick"
          >
            {Math.round(yMax * frac)}
          </text>
        </g>
      ))}
      {xLabels.map((label, index) =>
        index % labelStep === 0 ? (
          <text
            key={`${label}-${index}`}
            x={xFor(index)}
            y={height - 6}
            textAnchor="middle"
            className="mesh-analytics__tick"
          >
            {label}
          </text>
        ) : null,
      )}
      {series.map((s) => {
        const d = s.points
          .map((p, i) => `${i === 0 ? 'M' : 'L'}${xFor(p.x).toFixed(1)},${yFor(p.y).toFixed(1)}`)
          .join(' ');
        return (
          <g key={s.name}>
            <title>{s.name}</title>
            <path
              d={d}
              fill="none"
              stroke={TOKEN_VAR[s.colorToken]}
              strokeWidth={2}
              strokeDasharray={s.dashed === true ? '6 4' : undefined}
              data-testid={`analytics-line-${s.name}`}
            />
          </g>
        );
      })}
    </svg>
  );
}

export interface BarGroup {
  readonly label: string;
  readonly bars: ReadonlyArray<{
    readonly name: string;
    readonly value: number;
    readonly colorToken: ChartColorToken;
  }>;
}

export interface GroupedBarChartProps {
  readonly groups: readonly BarGroup[];
  readonly ariaLabel: string;
  readonly width?: number;
  readonly height?: number;
}

/** 分组柱状图:每组内并列多柱;柱顶数值 + <title> 文本兜底。 */
export function GroupedBarChart(props: GroupedBarChartProps): React.JSX.Element {
  const { groups, ariaLabel } = props;
  const width = props.width ?? 560;
  const height = props.height ?? 220;
  const yMax = niceMax(
    groups.reduce((acc, g) => Math.max(acc, ...g.bars.map((b) => b.value)), 0),
  );
  const innerW = width - PAD_LEFT - PAD_RIGHT;
  const innerH = height - PAD_TOP - PAD_BOTTOM;
  const groupWidth = innerW / Math.max(1, groups.length);
  const barCountMax = Math.max(1, ...groups.map((g) => g.bars.length));
  const barWidth = Math.max(4, (groupWidth * 0.7) / barCountMax);

  return (
    <svg
      className="mesh-analytics__chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel}
      data-testid="analytics-bar-chart"
    >
      <line
        x1={PAD_LEFT}
        x2={width - PAD_RIGHT}
        y1={PAD_TOP + innerH}
        y2={PAD_TOP + innerH}
        className="mesh-analytics__grid"
      />
      {groups.map((group, gi) => {
        const groupX = PAD_LEFT + gi * groupWidth + groupWidth * 0.15;
        return (
          <g key={`${group.label}-${gi}`}>
            <text
              x={PAD_LEFT + gi * groupWidth + groupWidth / 2}
              y={height - 6}
              textAnchor="middle"
              className="mesh-analytics__tick"
            >
              {group.label}
            </text>
            {group.bars.map((bar, bi) => {
              const barHeight = (bar.value / yMax) * innerH;
              const x = groupX + bi * (barWidth + 2);
              return (
                <g key={bar.name}>
                  <title>{`${group.label} · ${bar.name}: ${bar.value}`}</title>
                  <rect
                    x={x}
                    y={PAD_TOP + innerH - barHeight}
                    width={barWidth}
                    height={Math.max(1, barHeight)}
                    fill={TOKEN_VAR[bar.colorToken]}
                    data-testid={`analytics-bar-${bar.name}`}
                  />
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

export interface SparklineProps {
  readonly values: readonly number[];
  readonly colorToken: ChartColorToken;
  readonly ariaLabel: string;
  readonly width?: number;
  readonly height?: number;
}

/** 迷你趋势线(近 N 天执行趋势等);无轴,纯趋势信号 + 文本兜底。 */
export function Sparkline(props: SparklineProps): React.JSX.Element {
  const { values, colorToken, ariaLabel } = props;
  const width = props.width ?? 120;
  const height = props.height ?? 28;
  if (values.length === 0) {
    return (
      <svg
        className="mesh-analytics__sparkline"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={ariaLabel}
        data-testid="analytics-sparkline"
      />
    );
  }
  const max = Math.max(1, ...values);
  const step = values.length <= 1 ? 0 : width / (values.length - 1);
  const d = values
    .map((v, i) => {
      const x = values.length <= 1 ? width / 2 : i * step;
      const y = height - 2 - (v / max) * (height - 4);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <svg
      className="mesh-analytics__sparkline"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel}
      data-testid="analytics-sparkline"
    >
      <path d={d} fill="none" stroke={TOKEN_VAR[colorToken]} strokeWidth={1.5} />
    </svg>
  );
}
