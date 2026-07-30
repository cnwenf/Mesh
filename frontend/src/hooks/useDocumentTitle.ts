/**
 * useDocumentTitle — 路由级浏览器标签页标题(design-quality G19)。
 *
 * 全站标签页标题此前为静态(G19:多标签工作流无辨识度)。本 hook 让每个页面
 * 把自身的语义标题(实体页带 identifier/名称)写入 `document.title`,统一采用
 * 「<页面标题> · <产品名>」格式;页面标题为空时仅保留产品名。
 *
 * 设计:
 * - 仅同步 `document.title`(纯浏览器副作用,无 SSR);
 * - `title` 变化即更新(如 issue 标识异步解析后补题);
 * - 卸载时复位为产品名,避免离开页面后残留上一标题(多标签各自独立,但同一
 *   标签内路由切换需保持标题与当前页一致)。
 */
import { useEffect } from 'react';

/** 默认产品名(标签页标题后缀);可经第二参覆盖以便测试与白标。 */
export const DEFAULT_PRODUCT_NAME = 'Mesh';

/**
 * 设置当前页面的标签页标题。
 * @param title - 页面语义标题(已本地化);为空/空白时仅显示产品名。
 * @param productName - 标题后缀,默认 `Mesh`。
 */
export function useDocumentTitle(title: string, productName: string = DEFAULT_PRODUCT_NAME): void {
  useEffect(() => {
    const trimmed = title.trim();
    document.title = trimmed.length === 0 ? productName : `${trimmed} · ${productName}`;
    return () => {
      document.title = productName;
    };
  }, [title, productName]);
}
