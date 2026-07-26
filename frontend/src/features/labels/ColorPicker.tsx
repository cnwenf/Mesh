/**
 * 标签/选项颜色选择器(label-property.md §4.2 颜色选择)。
 * 设计系统无颜色选择构件,按 §6.12 就地实现:预设色板单选 + 自定义 hex 输入。
 * §6.12:颜色不得作为唯一信号——每个色块伴随 hex 文本,选中态有 aria 与描边。
 */
import { Input } from '../../design';

/** 预设色板(语义中性;两套主题下均满足与表面色的对比要求)。 */
export const PRESET_COLORS: readonly string[] = [
  '#e5484d',
  '#f5a623',
  '#f7d154',
  '#46a758',
  '#29a383',
  '#0ea5e9',
  '#3e63dd',
  '#8e4ec6',
  '#d6409f',
  '#8d8d86',
];

const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

export function isValidHexColor(value: string): boolean {
  return HEX_COLOR_PATTERN.test(value);
}

interface ColorPickerProps {
  readonly label: string;
  readonly value: string;
  readonly onChange: (color: string) => void;
  readonly error?: string;
  readonly hexInputLabel: string;
}

export function ColorPicker(props: ColorPickerProps): React.JSX.Element {
  const { label, value, onChange, error, hexInputLabel } = props;
  return (
    <div className="mesh-labels__color-picker" data-testid="color-picker">
      <span className="mesh-labels__color-picker-label" id="mesh-labels-color-group">
        {label}
      </span>
      <div
        role="radiogroup"
        aria-labelledby="mesh-labels-color-group"
        className="mesh-labels__swatches"
      >
        {PRESET_COLORS.map((color) => (
          <label key={color} className="mesh-labels__swatch" title={color}>
            <input
              type="radio"
              name="mesh-labels-color"
              value={color}
              checked={value.toLowerCase() === color.toLowerCase()}
              aria-label={color}
              onChange={() => onChange(color)}
            />
            <span
              className="mesh-labels__swatch-dot"
              style={{ backgroundColor: color }}
              aria-hidden="true"
            />
            <span className="mesh-labels__swatch-hex">{color}</span>
          </label>
        ))}
      </div>
      <Input
        label={hexInputLabel}
        value={value}
        placeholder="#RRGGBB"
        error={error}
        data-testid="color-hex-input"
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}
