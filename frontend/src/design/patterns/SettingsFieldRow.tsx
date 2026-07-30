/**
 * SettingsFieldRow — 设置表单的「label + control + hint」行(design-quality.md §7.4)。
 *
 * 轻量布局容器:可选可见字段 label、控件(children)、可选 hint。控件多为自标注的
 * design Select/Input(自带 <label>),此时省略本组件的 `label`,仅用 hint 行补充说明;
 * 控件为裸控件时经 `label` 提供可见字段名。无硬编码文案与色值。
 */
import type { ReactNode } from 'react';
import './patterns.css';

export interface SettingsFieldRowProps {
  /** 可见字段 label(控件已自标注时可省略) */
  label?: string;
  /** 控件下方 hint */
  hint?: string;
  /** 控件本体 */
  children: ReactNode;
}

export function SettingsFieldRow(props: SettingsFieldRowProps): React.JSX.Element {
  const { label, hint, children } = props;
  return (
    <div className="mesh-settings-field-row">
      {label !== undefined ? <span className="mesh-settings-field-row__label">{label}</span> : null}
      {children}
      {hint !== undefined ? <p className="mesh-settings-field-row__hint">{hint}</p> : null}
    </div>
  );
}
