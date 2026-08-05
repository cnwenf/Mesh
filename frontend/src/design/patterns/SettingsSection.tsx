/**
 * SettingsSection — 设置内容列的卡片表面分区(design-quality.md §4.4 / §7.4)。
 *
 * - 统一标题(h2,页面 h1 由 SettingsLayout 提供)+ 描述 + body + 可选 footer(dirty/save 区)。
 * - `tone='danger'`:边界与标题着危险语义 token(§3.2 危险区独立、与普通偏好拉开距离);
 *   颜色不作唯一信号,调用方需配图标/文案与确认流程(§7.3 danger)。
 * - 无硬编码文案与色值。
 */
import type { ReactNode } from 'react';
import './patterns.css';

export type SettingsSectionTone = 'default' | 'danger';
export type SettingsSectionLayout = 'stack' | 'rows';

export interface SettingsSectionProps {
  /** 分区标题(h2) */
  title: string;
  /** 分区描述(可选) */
  description?: string;
  /** 分区主体 */
  children: ReactNode;
  /** 底部操作区(dirty/save,可选;渲染分隔线之上) */
  footer?: ReactNode;
  /** 语义色调,默认 default */
  tone?: SettingsSectionTone;
  /** rows 将字段排成桌面双列、compact 单列的紧凑设置行 */
  layout?: SettingsSectionLayout;
}

export function SettingsSection(props: SettingsSectionProps): React.JSX.Element {
  const { title, description, children, footer, tone = 'default', layout = 'stack' } = props;
  const className = [
    'mesh-settings-section',
    tone === 'danger' ? 'mesh-settings-section--danger' : null,
    layout === 'rows' ? 'mesh-settings-section--rows' : null,
  ]
    .filter((part): part is string => part !== null)
    .join(' ');

  return (
    <section className={className} aria-label={title}>
      <div className="mesh-settings-section__header">
        <h2 className="mesh-settings-section__title">{title}</h2>
        {description !== undefined ? (
          <p className="mesh-settings-section__description">{description}</p>
        ) : null}
      </div>
      <div className="mesh-settings-section__panel">
        <div className="mesh-settings-section__body">{children}</div>
        {footer !== undefined ? (
          <div className="mesh-settings-section__footer">{footer}</div>
        ) : null}
      </div>
    </section>
  );
}
