/**
 * 键盘入口可发现性本地记忆(onboarding.md §4.2,L513)。
 *
 * Spec 约束:「已关闭/已使用过即不再出现(本地记忆,不进服务端,不统计)」——
 * 仅用 localStorage,无任何网络副作用。存储不可用(隐私模式/配额)时降级为
 * 「本会话视为未关闭」:提示可能再次出现,但绝不抛错阻断交互。
 */

const KEYBOARD_HINT_STORAGE_KEY = 'mesh.onboarding.keyboardHint.dismissed.v1';

/** 是否已记忆(曾关闭提示,或曾使用过命令面板/快捷键帮助层)。 */
export function isKeyboardHintDismissed(): boolean {
  try {
    return window.localStorage.getItem(KEYBOARD_HINT_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

/**
 * 落记忆(幂等)。关闭按钮与「已使用」(打开命令面板/帮助层/统一搜索)共用:
 * 两条路径在用户视角等价——都用过效率入口,提示使命即达成。
 */
export function dismissKeyboardHint(): void {
  try {
    window.localStorage.setItem(KEYBOARD_HINT_STORAGE_KEY, '1');
  } catch {
    // 存储不可用:组件以自身 state 保证本会话不再呈现,静默降级。
  }
}
