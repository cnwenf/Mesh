/**
 * 弹层 Esc 分层关闭栈(README §6.12 / search-command-palette.md §4.5,评审 P3)。
 *
 * 任一弹层(Dialog / 命令面板 / 帮助层 / 详情抽屉 / 弹层内子弹层)打开时经
 * pushOverlay 登记,卸载即移除;栈严格 LIFO。快捷键分发层(ShortcutProvider)
 * 在栈非空时全屏蔽背景页面快捷键,仅弹层自身键绑定与 Esc 语义生效:
 *
 * - 弹层内输入控件获焦时,首个 Esc **仅失焦输入控件**,不关弹层;
 * - 否则每次 Esc 只关最顶层;
 * - 关闭后焦点归还该层触发元素;触发元素已不存在时回落页面主区域(main)
 *   首个可聚焦元素,**绝不得落到 body**。
 */

export interface OverlayEntry {
  /** 稳定 id(同一弹层重复 push 以 id 去重) */
  readonly id: string;
  /** 打开该层时的触发元素(关闭后焦点归还目标) */
  readonly returnFocusTo: Element | null;
  /** 弹层自身键绑定(可选);分发层在屏蔽背景键前先调用 */
  readonly onKeyDown?: (event: KeyboardEvent) => void;
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

const stack: OverlayEntry[] = [];

/** 焦点目标是否为输入控件(input/textarea/select/contentEditable)。 */
export function isFormFieldElement(target: EventTarget | Element | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

export function pushOverlay(entry: OverlayEntry): () => void {
  const existingIndex = stack.findIndex((item) => item.id === entry.id);
  if (existingIndex >= 0) {
    stack.splice(existingIndex, 1);
  }
  stack.push(entry);
  return () => removeOverlay(entry.id);
}

export function removeOverlay(id: string): void {
  const index = stack.findIndex((item) => item.id === id);
  if (index >= 0) {
    stack.splice(index, 1);
  }
}

/** 测试/诊断用:当前栈深。 */
export function overlayDepth(): number {
  return stack.length;
}

export function isOverlayOpen(): boolean {
  return stack.length > 0;
}

export function topOverlay(): OverlayEntry | null {
  return stack.length > 0 ? stack[stack.length - 1] : null;
}

/**
 * 焦点归还(§6.12 焦点管理):优先触发元素;已不在文档中时回落 main 首个
 * 可聚焦元素;都没有则什么都不做(绝不 focus body)。
 */
export function restoreOverlayFocus(returnFocusTo: Element | null): void {
  if (returnFocusTo instanceof HTMLElement && returnFocusTo.isConnected) {
    returnFocusTo.focus();
    return;
  }
  const main = document.querySelector('main');
  if (main !== null) {
    const fallback = main.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    if (fallback !== null) {
      fallback.focus();
      return;
    }
    if (main instanceof HTMLElement) {
      // main 本身可聚焦时作为末位落点(仍非 body)。
      if (main.getAttribute('tabindex') !== null) {
        main.focus();
      }
    }
  }
}

/**
 * 分层关闭栈的 Esc 语义。栈空 → false(不处理)。
 * 弹层内输入控件获焦 → 仅失焦,返回 true(不关层);
 * 否则弹出栈顶并归还焦点,返回 true。
 */
export function handleOverlayEscape(): boolean {
  const top = topOverlay();
  if (top === null) {
    return false;
  }
  const active = document.activeElement;
  if (isFormFieldElement(active)) {
    (active as HTMLElement).blur();
    return true;
  }
  removeOverlay(top.id);
  restoreOverlayFocus(top.returnFocusTo);
  return true;
}
