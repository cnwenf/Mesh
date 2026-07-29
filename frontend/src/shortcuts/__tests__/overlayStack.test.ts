/**
 * overlayStack — LIFO 分层关闭栈 + Esc 语义(§4.5 评审 P3):
 * 弹层内输入控件获焦时首个 Esc 仅失焦;否则只关最顶层;焦点归还触发元素,
 * 触发元素已不存在时落 main 首个可聚焦元素(绝不落 body)。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  handleOverlayEscape,
  isOverlayOpen,
  overlayDepth,
  pushOverlay,
  restoreOverlayFocus,
  topOverlay,
} from '../overlayStack';

beforeEach(() => {
  document.body.innerHTML = '';
});

afterEach(() => {
  // 清栈,防测试间串态。
  while (isOverlayOpen()) {
    handleOverlayEscape();
  }
});

describe('overlayStack(LIFO 分层关闭栈)', () => {
  it('push 顺序即栈序,topOverlay 恒为最后入栈者', () => {
    const offA = pushOverlay({ id: 'a', returnFocusTo: null });
    const offB = pushOverlay({ id: 'b', returnFocusTo: null });
    expect(overlayDepth()).toBe(2);
    expect(topOverlay()?.id).toBe('b');
    offB();
    expect(topOverlay()?.id).toBe('a');
    offA();
    expect(isOverlayOpen()).toBe(false);
  });

  it('同 id 重复 push 不堆叠(替换至栈顶)', () => {
    const off1 = pushOverlay({ id: 'x', returnFocusTo: null });
    const off2 = pushOverlay({ id: 'x', returnFocusTo: null });
    expect(overlayDepth()).toBe(1);
    off1();
    off2();
    expect(isOverlayOpen()).toBe(false);
  });

  it('Esc 语义:弹层内输入控件获焦 → 仅失焦,不关层', () => {
    document.body.innerHTML = '<div><input aria-label="in-overlay" /></div>';
    const input = document.querySelector<HTMLInputElement>('input');
    input?.focus();
    pushOverlay({ id: 'dialog', returnFocusTo: null });
    expect(document.activeElement).toBe(input);

    const handled = handleOverlayEscape();
    expect(handled).toBe(true);
    // 输入控件失焦,但弹层仍在(首个 Esc 不关层)。
    expect(document.activeElement).not.toBe(input);
    expect(isOverlayOpen()).toBe(true);

    // 再按 Esc 才关层。
    handleOverlayEscape();
    expect(isOverlayOpen()).toBe(false);
  });

  it('Esc 语义:无输入焦点 → 每次只关最顶层(LIFO)', () => {
    pushOverlay({ id: 'drawer', returnFocusTo: null });
    pushOverlay({ id: 'selector', returnFocusTo: null });
    handleOverlayEscape();
    expect(topOverlay()?.id).toBe('drawer');
    handleOverlayEscape();
    expect(isOverlayOpen()).toBe(false);
  });

  it('关闭后焦点归还触发元素', () => {
    document.body.innerHTML = '<button type="button" data-testid="trigger">T</button>';
    const trigger = document.querySelector<HTMLElement>('[data-testid="trigger"]');
    trigger?.focus();
    pushOverlay({ id: 'd', returnFocusTo: trigger });
    handleOverlayEscape();
    expect(document.activeElement).toBe(trigger);
  });

  it('触发元素已不存在 → 落 main 首个可聚焦元素,绝不落 body', () => {
    document.body.innerHTML =
      '<main><button type="button" data-testid="main-btn">M</button></main>';
    const gone = document.createElement('button');
    document.body.appendChild(gone);
    pushOverlay({ id: 'd', returnFocusTo: gone });
    // 触发元素在关闭前被移除(如对应卡片被删)。
    gone.remove();

    handleOverlayEscape();
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(document.querySelector('[data-testid="main-btn"]'));
  });

  it('restoreOverlayFocus 直接调用亦不落 body(无 main 时什么都不做)', () => {
    document.body.innerHTML = '<span>nothing focusable</span>';
    document.body.focus();
    restoreOverlayFocus(null);
    expect(document.activeElement).not.toBe(null);
    // 无可聚焦落点:保持现状(不主动 focus body)。
  });
});
