/**
 * 图表卡片框架(design-quality.md §3.2 Analytics 行):标题 + 图例 + 图表 +
 * 口径注记的统一外壳。图例同时给颜色色块、线型(虚/实)与文字标签——
 * 颜色不作唯一信号(analytics.md §4.5);卡片最小宽度保证重排不压缩文字(§8.3)。
 */
import type { ReactNode } from 'react';
import { useT } from '../../i18n';
import { TOKEN_VAR } from './charts';
import type { ChartColorToken } from './charts';

export type LegendLineStyle = 'solid' | 'dashed';

/** 图元种类:line 给线型注词(虚线/实线);bar 给实心色块,无线型注词。 */
export type LegendMarkKind = 'line' | 'bar';

export interface ChartLegendItem {
  readonly label: string;
  readonly colorToken: ChartColorToken;
  /** 线型信号:虚线/实线;缺省 solid(仅 line 种类) */
  readonly lineStyle?: LegendLineStyle;
  /** 图元种类;缺省 line */
  readonly mark?: LegendMarkKind;
}

function swatchClassOf(item: ChartLegendItem): string {
  if (item.mark === 'bar') return 'mesh-analytics__legend-swatch mesh-analytics__legend-swatch--bar';
  if (item.lineStyle === 'dashed') {
    return 'mesh-analytics__legend-swatch mesh-analytics__legend-swatch--dashed';
  }
  return 'mesh-analytics__legend-swatch';
}

/** 图例:色块 + 线型 + 文字三重信号(§4.5 颜色非唯一信号)。 */
export function ChartLegend(props: {
  readonly items: readonly ChartLegendItem[];
}): React.JSX.Element | null {
  const { items } = props;
  const t = useT();
  if (items.length === 0) return null;
  return (
    <div className="mesh-analytics__legend" aria-label={t('analytics.chart.legendLabel')}>
      {items.map((item) => {
        const isLine = item.mark !== 'bar';
        const dashed = item.lineStyle === 'dashed';
        const swatchStyle =
          item.mark === 'bar'
            ? { backgroundColor: TOKEN_VAR[item.colorToken] }
            : { borderTopColor: TOKEN_VAR[item.colorToken] };
        return (
          <span className="mesh-analytics__legend-item" key={item.label}>
            <span className={swatchClassOf(item)} style={swatchStyle} aria-hidden="true" />
            {item.label}
            {isLine ? (
              <span className="mesh-analytics__legend-style">
                {`(${t(dashed ? 'analytics.legend.dashed' : 'analytics.legend.solid')})`}
              </span>
            ) : null}
          </span>
        );
      })}
    </div>
  );
}

export interface ChartFrameProps {
  readonly title: string;
  readonly children: ReactNode;
  readonly legend?: readonly ChartLegendItem[];
  /** 口径/时间范围注记(卡片级) */
  readonly note?: string;
  /** section 的 data-testid */
  readonly testId?: string;
  readonly headerExtra?: ReactNode;
}

export function ChartFrame(props: ChartFrameProps): React.JSX.Element {
  const { title, children, legend, note, testId, headerExtra } = props;
  return (
    <section className="mesh-analytics__card" data-testid={testId}>
      <div className="mesh-analytics__card-header">
        <h2 className="mesh-analytics__card-title">{title}</h2>
        {headerExtra}
      </div>
      {children}
      {legend !== undefined ? <ChartLegend items={legend} /> : null}
      {note !== undefined && note !== '' ? (
        <p className="mesh-analytics__card-note">{note}</p>
      ) : null}
    </section>
  );
}
