/**
 * 键盘入口可发现性本地记忆测试(onboarding.md §4.2,L513):
 * 纯本地(localStorage)、不进服务端;存储不可用时降级为「本会话未关闭」且不抛错。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dismissKeyboardHint, isKeyboardHintDismissed } from '../keyboardHint';

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('keyboardHint 本地记忆', () => {
  it('初始未关闭(首次进入工作区可见提示)', () => {
    expect(isKeyboardHintDismissed()).toBe(false);
  });

  it('关闭后持久记忆(刷新/重进不再出现)', () => {
    dismissKeyboardHint();
    expect(isKeyboardHintDismissed()).toBe(true);
  });

  it('重复关闭幂等', () => {
    dismissKeyboardHint();
    dismissKeyboardHint();
    expect(isKeyboardHintDismissed()).toBe(true);
  });

  it('存储读取不可用(隐私模式)→ 视为未关闭,不抛错', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(isKeyboardHintDismissed()).toBe(false);
  });

  it('存储写入不可用 → 静默降级,不阻断交互', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => dismissKeyboardHint()).not.toThrow();
  });
});
