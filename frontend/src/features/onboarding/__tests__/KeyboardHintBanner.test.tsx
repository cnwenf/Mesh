/**
 * 键盘入口一次性内联提示组件测试(onboarding.md §4.2,L513):
 * 未关闭时呈现两个效率入口(命令面板组合键 + ? 帮助层)与关闭按钮;
 * 关闭即隐藏并本地记忆;已记忆则渲染为空。非遮罩 tour(§1.3 非目标)。
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { KeyboardHintBanner } from '../KeyboardHintBanner';
import { dismissKeyboardHint, isKeyboardHintDismissed } from '../keyboardHint';

afterEach(() => {
  window.localStorage.clear();
});

describe('KeyboardHintBanner(onboarding.md §4.2)', () => {
  it('未关闭时呈现命令面板组合键与 ? 帮助层提示', () => {
    renderWithProviders(<KeyboardHintBanner />);
    const hint = screen.getByTestId('keyboard-hint');
    // jsdom navigator.platform 为空 → 非 mac 组合键形态
    expect(hint.textContent).toContain('Ctrl+K');
    expect(hint.textContent).toContain('?');
    // 关闭按钮在场(Banner onDismiss 通道,可访问名来自 i18n)
    expect(screen.getByRole('button', { name: 'Got it' })).toBeInTheDocument();
  });

  it('关闭后隐藏并持久记忆(重进不再出现)', async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<KeyboardHintBanner />);
    await user.click(screen.getByRole('button', { name: 'Got it' }));
    expect(screen.queryByTestId('keyboard-hint')).not.toBeInTheDocument();
    expect(isKeyboardHintDismissed()).toBe(true);
    // 模拟重进(重新挂载):不再渲染
    unmount();
    renderWithProviders(<KeyboardHintBanner />);
    expect(screen.queryByTestId('keyboard-hint')).not.toBeInTheDocument();
  });

  it('已记忆(曾关闭/曾使用)→ 不再渲染提示', () => {
    dismissKeyboardHint();
    renderWithProviders(<KeyboardHintBanner />);
    expect(screen.queryByTestId('keyboard-hint')).not.toBeInTheDocument();
  });
});
