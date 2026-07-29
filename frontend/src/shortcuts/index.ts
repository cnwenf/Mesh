/**
 * 快捷键体系公共 API(README §6.12)。
 */
export {
  CONTEXT_SPECIFICITY,
  arbitrateShortcut,
  isContextActive,
  useShortcutRegistry,
} from './registry';
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
export {
  handleOverlayEscape,
  isFormFieldElement,
  isOverlayOpen,
  overlayDepth,
  pushOverlay,
  removeOverlay,
  restoreOverlayFocus,
  topOverlay,
} from './overlayStack';
export type { OverlayEntry } from './overlayStack';
export { usePageContext } from './usePageContext';
export { SHORTCUT_DECLS } from './shortcutDefs';
export type { ShortcutDecl } from './shortcutDefs';
