/**
 * 颜色选择器测试:预设色板选择、hex 输入联动、格式校验。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { ColorPicker, PRESET_COLORS, isValidHexColor } from '../ColorPicker';

function renderPicker(value: string, onChange: (color: string) => void) {
  return render(
    <I18nProvider workspaceDefaultLocale={null} reporter={{ report: () => undefined, reported: [] }}>
      <ColorPicker
        label="Color"
        hexInputLabel="Hex value"
        value={value}
        onChange={onChange}
      />
    </I18nProvider>,
  );
}

describe('isValidHexColor', () => {
  it('accepts #RRGGBB only', () => {
    expect(isValidHexColor('#e5484d')).toBe(true);
    expect(isValidHexColor('#ABCDEF')).toBe(true);
    expect(isValidHexColor('#fff')).toBe(false);
    expect(isValidHexColor('e5484d')).toBe(false);
    expect(isValidHexColor('#gggggg')).toBe(false);
    expect(isValidHexColor('')).toBe(false);
  });

  it('exposes a non-empty preset palette', () => {
    expect(PRESET_COLORS.length).toBeGreaterThan(5);
    for (const color of PRESET_COLORS) expect(isValidHexColor(color)).toBe(true);
  });
});

describe('ColorPicker', () => {
  it('selecting a preset swatch emits its color', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderPicker('#000000', onChange);
    await user.click(screen.getByRole('radio', { name: '#e5484d' }));
    expect(onChange).toHaveBeenCalledWith('#e5484d');
  });

  it('the matching preset is checked (color never the sole signal: hex text shown)', () => {
    renderPicker('#e5484d', () => undefined);
    const radio = screen.getByRole('radio', { name: '#e5484d' }) as HTMLInputElement;
    expect(radio.checked).toBe(true);
    expect(screen.getAllByText('#e5484d').length).toBeGreaterThan(0);
  });

  it('typing into the hex input emits the raw value', () => {
    const onChange = vi.fn();
    renderPicker('', onChange);
    // 受控输入(测试不持有 state),直接以 change 事件驱动。
    fireEvent.change(screen.getByTestId('color-hex-input'), { target: { value: '#123456' } });
    expect(onChange).toHaveBeenLastCalledWith('#123456');
  });
});
