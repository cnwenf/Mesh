/**
 * Switch(design-quality §9.1):二态开关,role=switch + aria-checked;
 * 视觉为轨道 + 拇指(选中 accent 轨道),颜色不作唯一信号——拇指位置即状态;
 * 原生 <button> 承载键盘(Space/Enter 切换)与 focus-visible 环;命中区 ≥44px 高;
 * label 经 aria-labelledby 关联(可选 description 经 describedby)。无硬编码文案。
 */
import { useId } from 'react';
import { Switch as AppicaSwitch } from '@appica/ui-react/switch';
import './components.css';

export interface SwitchProps {
  /** 可见标签(必填) */
  label: string;
  /** 次级说明插槽 */
  description?: string;
  /** 受控选中态 */
  checked: boolean;
  /** 切换回调 */
  onCheckedChange: (next: boolean) => void;
  /** 禁用(建议调用方同时给出原因文案,§9.1 disabled 应说明原因) */
  disabled?: boolean;
  /** 控件 id 前缀(缺省自动生成) */
  id?: string;
}

export function Switch(props: SwitchProps): React.JSX.Element {
  const { label, description, checked, onCheckedChange, disabled = false, id } = props;
  const autoId = useId();
  const switchId = id ?? `mesh-switch-${autoId}`;
  const labelId = `${switchId}-label`;
  const descriptionId = `${switchId}-description`;

  return (
    <div className="mesh-switch">
      <div className="mesh-switch__row">
        <span className="mesh-switch__text">
          <span id={labelId} className="mesh-switch__label">
            {label}
          </span>
          {description ? (
            <span id={descriptionId} className="mesh-switch__description">
              {description}
            </span>
          ) : null}
        </span>
        <AppicaSwitch
          render={<button type="button" />}
          nativeButton
          id={switchId}
          className={checked ? 'mesh-switch__control mesh-switch__control--on' : 'mesh-switch__control'}
          aria-labelledby={labelId}
          aria-describedby={description ? descriptionId : undefined}
          checked={checked}
          disabled={disabled}
          onCheckedChange={(next) => onCheckedChange(next)}
          size="lg"
        />
      </div>
    </div>
  );
}
