/**
 * useDocumentTitle — 动态浏览器标签页标题(G19,design-quality 完成品基线)。
 *
 * 契约:
 * - 标题形态为「<页面语义段> · Mesh」,如「MES-123 修复登录 · Mesh」;
 * - 传入的 parts 中 undefined / 空串自动跳过(数据未就绪时标题平滑降级);
 * - 无有效 part 时回落到应用名 Mesh;
 * - 组件卸载时还原为挂载前的标题(多级页面切换不叠加、不丢失)。
 *
 * 用法:页面组件顶部 `useDocumentTitle(issueIdentifier, issueTitle)` 即可,
 * 各批次页面统一经本 hook 接入(shell/hooks 导出,MES-127 批次④)。
 */
import { useEffect } from 'react';

/** 应用名后缀:一切页面标题的统一品牌尾段。 */
export const APP_TITLE_SUFFIX = 'Mesh';

/** 标题连接符:语义段之间与品牌尾段共用「 · 」。 */
const TITLE_SEPARATOR = ' · ';

/**
 * 把语义段组装为最终标签页标题。
 * @param parts - 语义段(如 identifier、对象名、页面名);undefined / 空白段被过滤
 * @returns 「seg1 · seg2 · Mesh」;无有效段时仅「Mesh」
 */
export function composeDocumentTitle(parts: ReadonlyArray<string | undefined>): string {
  const segments = parts
    .filter((part): part is string => typeof part === 'string' && part.trim().length > 0)
    .map((part) => part.trim());
  if (segments.length === 0) {
    return APP_TITLE_SUFFIX;
  }
  return [...segments, APP_TITLE_SUFFIX].join(TITLE_SEPARATOR);
}

/**
 * 设置当前页面的 document.title,卸载时还原。
 * @param parts - 页面语义段(按展示顺序);随渲染更新(如标题加载完成后补入)
 */
export function useDocumentTitle(...parts: ReadonlyArray<string | undefined>): void {
  const title = composeDocumentTitle(parts);

  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
