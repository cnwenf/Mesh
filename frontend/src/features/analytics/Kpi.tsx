/**
 * 单个 KPI 指标单元(design-quality.md §3.2 Analytics 行 / §6.3):
 * 大数字走 title 字阶 + tabular-nums(.mesh-tnum,数字列对齐不跳动);
 * 标签走 caption 字阶;hint 承载口径/时间范围——大数字不允许孤立数字(§6.3)。
 * tone 仅作语义增强,含义始终有 label/hint 文本兜底(颜色非唯一信号,§4.5)。
 */
import type { ReactNode } from 'react';

export type KpiTone = 'default' | 'success' | 'warning' | 'danger';

export interface KpiProps {
  /** 指标名(caption 字阶) */
  readonly label: string;
  /** 数值;数字默认等宽数字位渲染 */
  readonly value: string | number;
  /** 单位(小字随值,如 项 / %) */
  readonly unit?: string;
  /** 语义色调;缺省 default(颜色非唯一信号) */
  readonly tone?: KpiTone;
  /** 口径/时间范围说明(大数字不孤立) */
  readonly hint?: string;
  /** 数字等宽位;缺省 true(§6.3) */
  readonly tabular?: boolean;
}

/** 值 + 单位 的纯展示片段(供 Kpi 与内嵌复用)。 */
export function KpiValue(props: {
  readonly value: string | number;
  readonly unit?: string;
  readonly tone: KpiTone;
  readonly tabular: boolean;
}): ReactNode {
  const { value, unit, tone, tabular } = props;
  const classes = [
    'mesh-analytics__kpi-big',
    tabular ? 'mesh-tnum' : '',
    tone !== 'default' ? `mesh-analytics__kpi-big--${tone}` : '',
  ]
    .filter((c) => c !== '')
    .join(' ');
  return (
    <p className={classes}>
      {value}
      {unit !== undefined && unit !== '' ? (
        <span className="mesh-analytics__kpi-unit">{unit}</span>
      ) : null}
    </p>
  );
}

export function Kpi(props: KpiProps): React.JSX.Element {
  const { label, value, unit, hint } = props;
  const tone = props.tone ?? 'default';
  const tabular = props.tabular ?? true;
  return (
    <div className="mesh-analytics__kpi-cell">
      <p className="mesh-analytics__kpi-caption">{label}</p>
      <KpiValue value={value} unit={unit} tone={tone} tabular={tabular} />
      {hint !== undefined && hint !== '' ? (
        <p className="mesh-analytics__kpi-hint">{hint}</p>
      ) : null}
    </div>
  );
}
