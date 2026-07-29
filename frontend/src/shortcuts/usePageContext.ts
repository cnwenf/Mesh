/**
 * 页面上下文激活钩子(search-command-palette.md §2.1 / §5.1:setContexts 死代码接通)。
 *
 * 路由/页面挂载时把该页上下文组写入注册表 activeContexts(global 恒激活,
 * 自动前置);卸载复位为 ['global']。特异性序与 chat 独占语义由调用方选取
 * 的上下文表达:BoardPage → usePageContext('board');IssueDetailPage →
 * usePageContext('board', 'issue')(issue 仲裁胜出);ChatPage →
 * usePageContext('chat')(独占,不叠加 board/issue)。
 */
import { useEffect } from 'react';
import { useShortcutRegistry } from './registry';
import type { ShortcutContext } from './registry';

export function usePageContext(
  ...contexts: ReadonlyArray<Exclude<ShortcutContext, 'global'>>
): void {
  // 稳定化依赖:上下文集合按字面量传入,以 join 键避免每次渲染重建 effect。
  const contextsKey = contexts.join('|');
  useEffect(() => {
    const active = contextsKey === '' ? [] : (contextsKey.split('|') as ShortcutContext[]);
    useShortcutRegistry.getState().setContexts(['global', ...active]);
    return () => {
      useShortcutRegistry.getState().setContexts(['global']);
    };
  }, [contextsKey]);
}
