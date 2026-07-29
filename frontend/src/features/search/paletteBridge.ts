/**
 * 顶栏 ↔ 命令面板的查询桥(search-command-palette.md §4.9:输入即展开同一结果视图)。
 *
 * App 层持有面板 open 态且 openPalette() 无参(不改动 App.tsx),故以模块级
 * 外部存储传递「打开时携带的初始查询」:TopBar 输入/提交时 setPaletteQuery(q)
 * 再 openPalette();面板打开瞬间 takePaletteQuery() 消费并清空(避免下次
 * Ctrl/Cmd+K 空开时残留旧查询)。useSyncExternalStore 订阅保证并发渲染安全。
 */
import { useSyncExternalStore } from 'react';

type Listener = () => void;

let currentQuery = '';
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

/** 设置待面板消费的初始查询(顶栏输入/提交调用) */
export function setPaletteQuery(query: string): void {
  if (currentQuery === query) return;
  currentQuery = query;
  emit();
}

/** 读取当前桥接查询(不消费) */
export function getPaletteQuery(): string {
  return currentQuery;
}

/** 消费当前桥接查询:返回并清空(面板打开时调用,保证一次性语义) */
export function takePaletteQuery(): string {
  const taken = currentQuery;
  if (taken !== '') {
    currentQuery = '';
    emit();
  }
  return taken;
}

/** 订阅桥接查询变化;返回取消订阅函数 */
export function subscribePaletteQuery(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 响应式读取桥接查询(并发渲染安全) */
export function usePaletteBridgeQuery(): string {
  return useSyncExternalStore(subscribePaletteQuery, getPaletteQuery, () => '');
}
