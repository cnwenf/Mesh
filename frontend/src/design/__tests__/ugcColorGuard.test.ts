/**
 * UGC 内联色对比兜底(theme.md §4.3 T5③):对比不足回退 var(--color-text),
 * 主题切换经 THEME_CHANGED_EVENT 重扫;var()/不可解析值不参与。
 *
 * jsdom 不解析样式表自定义属性,用例以行内 --color-surface 提供表面色
 * (生产取自 tokens 级联,语义一致)。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { THEME_CHANGED_EVENT, guardUgcInlineColors, useUgcColorGuard } from '../ugcColorGuard';

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
