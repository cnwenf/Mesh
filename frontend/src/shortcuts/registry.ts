/**
 * 快捷键/命令注册表(README §6.12 快捷键体系)。
 *
 * - 命令(CommandPalette 条目)与快捷键(ShortcutDef)分组注册:global / board / issue / chat;
 * - 同 id 重复注册替换旧条目(幂等);注册返回注销函数;
 * - activeContexts 由 shell/页面层设置,帮助层与路由据此实时反映当前可用快捷键;
 * - 全部不可变更新(zustand)。
 */
import { create } from 'zustand';

export type ShortcutContext = 'global' | 'board' | 'issue' | 'chat';

export interface ShortcutCommand {
  id: string;
  label: string;
  group: ShortcutContext;
  /** 搜索关键词(命令面板过滤用) */
  keywords?: string[];
  /** 关联快捷键组合(展示用,可选) */
  combo?: string;
  run: () => void;
}

export interface ShortcutDef {
  id: string;
  /** 归一化组合键:'c'、'/'、'mod+k'、序列 'g i' 等 */
  combo: string;
  label: string;
  group: ShortcutContext;
  run: () => void;
}

export interface ShortcutRegistryState {
  commands: ReadonlyArray<ShortcutCommand>;
  shortcuts: ReadonlyArray<ShortcutDef>;
  activeContexts: ReadonlyArray<ShortcutContext>;
  /** 注册命令;返回注销函数。同 id 替换。 */
  registerCommand: (command: ShortcutCommand) => () => void;
  /** 批量注册快捷键;返回整体注销函数。同 id 替换。 */
  registerShortcuts: (defs: ReadonlyArray<ShortcutDef>) => () => void;
  /** 设置当前激活上下文(global 恒激活,无需列出)。 */
  setContexts: (contexts: ReadonlyArray<ShortcutContext>) => void;
}

export const useShortcutRegistry = create<ShortcutRegistryState>()((set) => ({
  commands: [],
  shortcuts: [],
  activeContexts: [],

  registerCommand: (command) => {
    set((state) => ({
      commands: [...state.commands.filter((item) => item.id !== command.id), command],
    }));
    return () =>
      set((state) => ({ commands: state.commands.filter((item) => item.id !== command.id) }));
  },

  registerShortcuts: (defs) => {
    const ids = new Set(defs.map((def) => def.id));
    set((state) => ({
      shortcuts: [...state.shortcuts.filter((item) => !ids.has(item.id)), ...defs],
    }));
    return () =>
      set((state) => ({ shortcuts: state.shortcuts.filter((item) => !ids.has(item.id)) }));
  },

  setContexts: (contexts) => set(() => ({ activeContexts: [...contexts] })),
}));
