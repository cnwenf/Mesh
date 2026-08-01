/**
 * useDocumentTitle(shell/hooks 变参语义段 API,G19)。
 *
 * 站点唯一实现为 `src/hooks/useDocumentTitle`(批次①已合入的权威 hook:
 * 「<语义标题> · Mesh」,卸载复位产品名)。本模块只在其上提供**多语义段组装**
 * 便捷层:实体页可传 `(工作区名, 页面名)` 等多段,undefined/空白段自动过滤,
 * 最终写入与复位统一经权威 hook——避免 G19 双实现漂移。
 *
 * 用法:页面组件顶部 `useDocumentTitle(workspaceName, t('...settingsTitle'))`。
 */
import {
  DEFAULT_PRODUCT_NAME,
  useDocumentTitle as useDocumentTitleBase,
} from '../../hooks/useDocumentTitle';

/** 应用名后缀:复用权威 hook 的产品名,保持全站一致。 */
export const APP_TITLE_SUFFIX = DEFAULT_PRODUCT_NAME;

/** 标题连接符:语义段之间共用「 · 」(品牌尾段由权威 hook 追加)。 */
const TITLE_SEPARATOR = ' · ';

/**
 * 把语义段组装为权威 hook 接受的语义标题。
 * @param parts - 语义段(如 工作区名、页面名);undefined / 空白段被过滤并 trim
 * @returns 「seg1 · seg2」;无有效段时为空串(权威 hook 回落产品名)
 */
export function composeDocumentTitle(parts: ReadonlyArray<string | undefined>): string {
  const segments = parts
    .filter((part): part is string => typeof part === 'string' && part.trim().length > 0)
    .map((part) => part.trim());
  return segments.join(TITLE_SEPARATOR);
}

/**
 * 设置当前页面的 document.title(经权威 hook:卸载复位产品名)。
 * @param parts - 页面语义段(按展示顺序);随渲染更新(如名称加载完成后补入)
 */
export function useDocumentTitle(...parts: ReadonlyArray<string | undefined>): void {
  useDocumentTitleBase(composeDocumentTitle(parts));
}
