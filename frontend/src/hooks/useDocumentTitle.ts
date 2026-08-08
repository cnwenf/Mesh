/**
 * useDocumentTitle — 路由级浏览器标签页标题(design-quality G19)。
 *
 * 全站标签页标题此前为静态(G19:多标签工作流无辨识度)。本 hook 让每个页面
 * 把自身的语义标题(实体页带 identifier/名称)写入 `document.title`,统一采用
 * 「<页面标题> · <产品名>」格式;页面标题为空时仅保留产品名。
 *
 * MES-189 L93:有未读通知时标题前缀 「(N) 」(与 favicon 徽标同源,经
 * state/unreadStore 镜像——权威计数在 InboxBell,见该 store 注释),多标签
 * 工作流中无需看 favicon 即可感知未读。
 *
 * 设计:
 * - 仅同步 `document.title`(纯浏览器副作用,无 SSR);
 * - `title` 变化即更新(如 issue 标识异步解析后补题);未读数变化同样即时更新;
 * - 卸载时复位为产品名,避免离开页面后残留上一标题(多标签各自独立,但同一
 *   标签内路由切换需保持标题与当前页一致)。
 */
import { useEffect } from 'react';
import { useUnreadStore } from '../state/unreadStore';

/** 默认产品名(标签页标题后缀);可经第二参覆盖以便测试与白标。 */
export const DEFAULT_PRODUCT_NAME = 'Mesh';

/**
 * 设置当前页面的标签页标题。
 * @param title - 页面语义标题(已本地化);为空/空白时仅显示产品名。
 * @param productName - 标题后缀,默认 `Mesh`。
 */
export function useDocumentTitle(title: string, productName: string = DEFAULT_PRODUCT_NAME): void {
  const unreadCount = useUnreadStore((state) => state.count);
  useEffect(() => {
    const trimmed = title.trim();
    const prefix = unreadCount > 0 ? `(${unreadCount}) ` : '';
    document.title =
      trimmed.length === 0 ? `${prefix}${productName}` : `${prefix}${trimmed} · ${productName}`;
    return () => {
      document.title = productName;
    };
  }, [title, productName, unreadCount]);
}
