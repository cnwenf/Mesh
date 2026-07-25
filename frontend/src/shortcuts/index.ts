/**
 * 快捷键体系公共 API(README §6.12)。
 */
export { useShortcutRegistry } from './registry';
export type {
  ShortcutCommand,
  ShortcutContext,
  ShortcutDef,
  ShortcutRegistryState,
} from './registry';
export { SEQUENCE_WINDOW_MS, ShortcutProvider, detectMac, formatCombo } from './ShortcutProvider';
export type { ShortcutProviderProps } from './ShortcutProvider';
export { CommandPalette } from './CommandPalette';
export type { CommandPaletteProps } from './CommandPalette';
export { ShortcutHelp } from './ShortcutHelp';
export type { ShortcutHelpProps } from './ShortcutHelp';
