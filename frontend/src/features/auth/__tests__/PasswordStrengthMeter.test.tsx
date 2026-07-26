import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { PasswordStrengthMeter } from '../PasswordStrengthMeter';

describe('PasswordStrengthMeter(auth.md §4.1 强度条 + 实时校验)', () => {
  it('空口令不渲染(不打扰未输入用户)', () => {
    renderWithProviders(<PasswordStrengthMeter password="" />);
    expect(screen.queryByTestId('password-strength')).toBeNull();
  });

  it('弱口令:低分档位 + 未满足规则实时提示', () => {
    renderWithProviders(<PasswordStrengthMeter password="a1" />);
    const meter = screen.getByTestId('password-strength');
    expect(meter.getAttribute('aria-valuenow')).toBe('1');
    expect(screen.getByTestId('password-strength-label').textContent).toContain('Weak');
    const rules = screen.getByTestId('password-rules');
    expect(rules.textContent).toContain('at least 8');
    expect(rules.textContent).not.toContain('letters and digits');
  });

  it('仅字母:提示需字母+数字', () => {
    renderWithProviders(<PasswordStrengthMeter password="onlyletters" />);
    expect(screen.getByTestId('password-rules').textContent).toContain('letters and digits');
  });

  it('常见弱口令:提示避免常见密码', () => {
    renderWithProviders(<PasswordStrengthMeter password="password123" />);
    expect(screen.getByTestId('password-rules').textContent).toContain('common passwords');
    expect(screen.getByTestId('password-strength-label').textContent).toContain('Weak');
  });

  it('合格口令:无规则提示,good 档位', () => {
    renderWithProviders(<PasswordStrengthMeter password="Tr5x9qLm2v" />);
    const meter = screen.getByTestId('password-strength');
    expect(meter.getAttribute('aria-valuenow')).toBe('3');
    expect(screen.getByTestId('password-strength-label').textContent).toContain('Good');
    expect(screen.queryByTestId('password-rules')).toBeNull();
  });

  it('长且合格口令:strong 档位 + 四段填充', () => {
    renderWithProviders(<PasswordStrengthMeter password="Tr5x9qLm2vBz" />);
    const meter = screen.getByTestId('password-strength');
    expect(meter.getAttribute('aria-valuenow')).toBe('4');
    expect(screen.getByTestId('password-strength-label').textContent).toContain('Strong');
    expect(meter.querySelectorAll('.is-filled')).toHaveLength(4);
  });
});
