/**
 * UGC 内联色对比兜底(theme.md §4.3 T5③):对比不足回退 var(--color-text),
 * 主题切换经 THEME_CHANGED_EVENT 重扫;var()/不可解析值不参与。
 *
 * jsdom 不解析样式表自定义属性,用例以行内 --color-surface 提供表面色
 * (生产取自 tokens 级联,语义一致)。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  THEME_CHANGED_EVENT,
  guardUgcInlineColors,
  rescanGuardedRefs,
  sweepGuardedRoots,
  useUgcColorGuard,
} from '../ugcColorGuard';

const DARK_SURFACE = '#1e293b';
const LIGHT_SURFACE = '#f9fafb';

function makeRoot(surface: string, innerHtml: string): HTMLElement {
  const root = document.createElement('div');
  root.style.setProperty('--color-surface', surface);
  root.innerHTML = innerHtml;
  document.body.appendChild(root);
  return root;
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('guardUgcInlineColors', () => {
  it('暗底低对比内联色(黑字)→ 回退 var(--color-text)', () => {
    const root = makeRoot(DARK_SURFACE, '<p style="color: #000000">低对比</p>');
    guardUgcInlineColors(root);
    const p = root.querySelector('p');
    expect(p?.style.color).toBe('var(--color-text)');
  });

  it('暗底高对比内联色(白字)→ 保留', () => {
    const root = makeRoot(DARK_SURFACE, '<p style="color: #ffffff">高对比</p>');
    guardUgcInlineColors(root);
    expect(root.querySelector('p')?.style.color).toBe('rgb(255, 255, 255)');
  });

  it('亮底黑字 → 保留(对比达标)', () => {
    const root = makeRoot(LIGHT_SURFACE, '<p style="color: #000000">正文</p>');
    guardUgcInlineColors(root);
    expect(root.querySelector('p')?.style.color).toBe('rgb(0, 0, 0)');
  });

  it('var() 取色不参与兜底(token 体系内)', () => {
    const root = makeRoot(DARK_SURFACE, '<p style="color: var(--color-primary)">x</p>');
    guardUgcInlineColors(root);
    expect(root.querySelector('p')?.style.color).toBe('var(--color-primary)');
  });

  it('rgb() 写法同样参与对比判定', () => {
    const root = makeRoot(DARK_SURFACE, '<p style="color: rgb(17, 24, 39)">x</p>');
    guardUgcInlineColors(root);
    expect(root.querySelector('p')?.style.color).toBe('var(--color-text)');
  });

  it('嵌套多个违规元素全部回退', () => {
    const root = makeRoot(
      DARK_SURFACE,
      '<div><span style="color:#111827">a</span><em style="color:#f8fafc">b</em></div>',
    );
    guardUgcInlineColors(root);
    expect(root.querySelector('span')?.style.color).toBe('var(--color-text)');
    expect(root.querySelector('em')?.style.color).toBe('rgb(248, 250, 252)');
  });

  it('不可解析的内联色(命名色)→ 不强改(保持净化器既有约束)', () => {
    const root = makeRoot(DARK_SURFACE, '<p style="color: red">x</p>');
    guardUgcInlineColors(root);
    // ratioOnSurface → null(fg 解析失败):保留原值,不误伤。
    expect(root.querySelector('p')?.style.color).toBe('red');
  });

  it('不可解析的表面色 → 整轮跳过(不抛、不改)', () => {
    const root = makeRoot('not-a-color', '<p style="color: #000000">x</p>');
    guardUgcInlineColors(root);
    expect(root.querySelector('p')?.style.color).toBe('rgb(0, 0, 0)');
  });

  it('半透明前景先对表面合成再判定(暗底半透明黑字 → 回退)', () => {
    // rgba(0,0,0,0.4) 叠 #1e293b 后仍是深灰 on 暗蓝,对比 <4.5 → 回退。
    const root = makeRoot(DARK_SURFACE, '<p style="color: rgba(0, 0, 0, 0.4)">x</p>');
    guardUgcInlineColors(root);
    expect(root.querySelector('p')?.style.color).toBe('var(--color-text)');
  });

  it('半透明表面色先对白底合成再判定(等效白底黑字 → 保留)', () => {
    // rgba(255,255,255,0.5) 叠 #ffffff = 白底;黑字对比 21:1 → 保留。
    const root = makeRoot('rgba(255, 255, 255, 0.5)', '<p style="color: #000000">x</p>');
    guardUgcInlineColors(root);
    expect(root.querySelector('p')?.style.color).toBe('rgb(0, 0, 0)');
  });
});

describe('useUgcColorGuard — 挂载兜底 + 主题变更重扫', () => {
  it('回调 ref 挂载时执行兜底;主题变更事件触发重扫', () => {
    // 亮底:黑字达标,保留。
    const root = makeRoot(LIGHT_SURFACE, '<p style="color: #000000">x</p>');
    const guard = useUgcColorGuard();
    guard(root);
    expect(root.querySelector('p')?.style.color).toBe('rgb(0, 0, 0)');

    // 切暗:同一内联黑字不达标,重扫后回退。
    root.style.setProperty('--color-surface', DARK_SURFACE);
    window.dispatchEvent(new CustomEvent(THEME_CHANGED_EVENT));
    expect(root.querySelector('p')?.style.color).toBe('var(--color-text)');
  });

  it('节点释放后重扫不崩溃', () => {
    const root = makeRoot(LIGHT_SURFACE, '<p style="color:#000000">x</p>');
    const guard = useUgcColorGuard();
    guard(root);
    root.remove();
    expect(() => window.dispatchEvent(new CustomEvent(THEME_CHANGED_EVENT))).not.toThrow();
  });
});

describe('rescanGuardedRefs — 存活重扫 / 已回收登记点收集', () => {
  it('存活节点执行重扫;已回收节点(WeakRef 空)收集返回', () => {
    // 存活登记点:亮底黑字先达标,改表面为暗底后经重扫回退。
    const root = makeRoot(LIGHT_SURFACE, '<p style="color:#000000">x</p>');
    const live = new WeakRef(root);
    // 已回收登记点:deref → undefined 的结构等价桩(GC 时机不可控,以桩收敛分支)。
    const reclaimedRef = { deref: () => undefined } as WeakRef<HTMLElement>;

    root.style.setProperty('--color-surface', DARK_SURFACE);
    const reclaimed = rescanGuardedRefs([reclaimedRef, live]);

    expect(reclaimed).toEqual([reclaimedRef]);
    expect(root.querySelector('p')?.style.color).toBe('var(--color-text)');
  });

  it('sweepGuardedRoots:重扫存活点并从登记集合剪除已回收点', () => {
    const root = makeRoot(LIGHT_SURFACE, '<p style="color:#000000">x</p>');
    const live = new WeakRef(root);
    const dead = { deref: () => undefined } as WeakRef<HTMLElement>;
    const registry = new Set<WeakRef<HTMLElement>>([dead, live]);

    root.style.setProperty('--color-surface', DARK_SURFACE);
    sweepGuardedRoots(registry);

    expect(registry.has(dead)).toBe(false);
    expect(registry.has(live)).toBe(true);
    expect(root.querySelector('p')?.style.color).toBe('var(--color-text)');
  });
});
