/**
 * UGC 内联色对比兜底(theme.md §4.3 评审 T5③ / §6.15 同边界:样式亦不可信)。
 *
 * 评论/描述等 UGC 的内联 `style` 颜色不参与 token 体系:其文本与当前表面色
 * (--color-surface)对比不足 4.5:1 时强制回退 `var(--color-text)`,防暗底黑字
 * 不可读。回退值取 token 变量,主题切换后自动保持正确;主题变更事件
 * (`mesh-theme-changed`,ThemeProvider 派发)触发已登记根节点重扫。
 */
import { compositeOver, parseColor, relativeLuminance } from './contrast';

/** 主题变更事件名(ThemeProvider 每次权威解析落地后派发) */
export const THEME_CHANGED_EVENT = 'mesh-theme-changed';

const WCAG_TEXT_MIN = 4.5;

const guardedRoots = new Set<WeakRef<HTMLElement>>();
let themeListenerInstalled = false;

function ratioOnSurface(fgText: string, surfaceHex: string): number | null {
  const fg = parseColor(fgText);
  const bg = parseColor(surfaceHex);
  if (fg === null || bg === null) return null;
  const composed = fg.a >= 1 ? fg : compositeOver(fg, surfaceHex);
  const bgComposed = bg.a >= 1 ? bg : compositeOver(bg, '#ffffff');
  const l1 = relativeLuminance(composed);
  const l2 = relativeLuminance(bgComposed);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * 扫描根节点内带内联 color 的元素,对比不足者回退 var(--color-text)。
 * 幂等:已回退为 var() 的值跳过;非静态值(var()/表达式)不参与。
 */
export function guardUgcInlineColors(root: HTMLElement): void {
  const surface = getComputedStyle(root).getPropertyValue('--color-surface').trim();
  if (surface.length === 0) return;
  const nodes = root.querySelectorAll<HTMLElement>('[style*="color"]');
  nodes.forEach((el) => {
    const inline = el.style.color;
    if (inline === '' || inline.includes('var(')) return;
    const ratio = ratioOnSurface(inline, surface);
    if (ratio === null) return; // 不可解析色值:保持净化器既有约束,不强改
    if (ratio < WCAG_TEXT_MIN) {
      el.style.setProperty('color', 'var(--color-text)');
    }
  });
}

/**
 * 逐个重扫登记点:存活节点执行兜底重扫;节点已被 GC 回收的登记点
 * 收集返回,由调用方移除。抽出纯函数供主题变更监听器复用(§4.3)。
 */
export function rescanGuardedRefs(
  refs: Iterable<WeakRef<HTMLElement>>,
): WeakRef<HTMLElement>[] {
  const reclaimed: WeakRef<HTMLElement>[] = [];
  for (const ref of refs) {
    const el = ref.deref();
    if (el === undefined) {
      reclaimed.push(ref);
    } else {
      guardUgcInlineColors(el);
    }
  }
  return reclaimed;
}

/** 重扫并剪除已回收登记点(缺省作用于模块登记表;主题变更监听器入口)。 */
export function sweepGuardedRoots(refs: Set<WeakRef<HTMLElement>> = guardedRoots): void {
  for (const ref of rescanGuardedRefs(refs)) {
    refs.delete(ref);
  }
}

function installThemeListener(): void {
  if (themeListenerInstalled || typeof window === 'undefined') return;
  themeListenerInstalled = true;
  window.addEventListener(THEME_CHANGED_EVENT, () => sweepGuardedRoots());
}

/**
 * React 回调 ref:UGC 容器挂载/内容替换时执行兜底,并登记主题变更重扫。
 * 用法:`<div ref={useUgcColorGuard()} dangerouslySetInnerHTML={...} />`
 */
export function useUgcColorGuard(): (node: HTMLElement | null) => void {
  return (node) => {
    if (node === null) return;
    guardUgcInlineColors(node);
    installThemeListener();
    guardedRoots.add(new WeakRef(node));
  };
}
