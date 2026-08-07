/**
 * 收件箱未读数全局镜像(MES-189 L93)。
 *
 * 权威源仍是 InboxBell 的计数状态(挂载时 REST unread-count 快照 +
 * inbox.unread_count 实时帧为唯一计数真源,comment-inbox.md §4.2);本 store
 * 只是把该计数分发给**全局 chrome**的镜像:标签页标题未读前缀
 * (src/hooks/useDocumentTitle)与 favicon 未读徽标(features/inbox/unreadFavicon)。
 * 这两个消费方不应依赖铃铛组件实例,更不该各自重复一份网络/频道订阅。
 *
 * 语义:仅镜像非负整数;setCount 夹取负值(乐观递减等路径的防御)。
 */
import { create } from 'zustand';

export interface UnreadState {
  /** 当前工作区未读通知数(0 = 无未读 / 未解析)。 */
  count: number;
  /** 由权威计数方(InboxBell)写入;负值夹取为 0。 */
  setCount: (count: number) => void;
}

export const useUnreadStore = create<UnreadState>()((set) => ({
  count: 0,
  setCount: (count) => set({ count: Math.max(0, count) }),
}));
