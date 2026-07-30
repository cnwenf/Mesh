/**
 * 快捷键体系公共 API(README §6.12 / search-command-palette.md)。
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
export { PaletteResults, badgeToneForColor, HighlightedTitle } from './PaletteResults';
export type { PaletteResultsProps } from './PaletteResults';
export { DEBOUNCE_MS, useEntitySearch } from './useEntitySearch';
export type { UseEntitySearchArgs, UseEntitySearchResult } from './useEntitySearch';
export { usePaletteContext, resetPaletteContextCache } from './usePaletteContext';
export type { PaletteContextValue } from './usePaletteContext';
export { defaultFavoritesProvider, isOfflineCondition, usePaletteData } from './usePaletteData';
export type { FavoritesProvider, UsePaletteDataArgs, UsePaletteDataResult } from './usePaletteData';
export {
  GROUP_LABEL_KEYS,
  TOP_COMMANDS_LIMIT,
  activatePaletteOption,
  buildEmptySections,
  buildQuerySections,
  commandStableId,
  entityStableId,
  filterCommands,
  flattenSections,
  iconForSemanticKey,
  moveSelection,
  optionDomId,
  reconcileSelection,
  subtitleForItem,
} from './paletteModel';
export type {
  ActivationDeps,
  ActivationOptions,
  EmptySectionsInput,
  PaletteGroupKey,
  PaletteOption,
  PaletteSection,
  PaletteSubtitle,
} from './paletteModel';
export {
  RECENTS_LIMIT,
  clearRecents,
  commandCountKey,
  commandUseCounts,
  getRecentsScope,
  listRecents,
  pushRecent,
  recentIdentity,
  recentsKey,
  removeRecent,
  setRecentsScope,
  stableHost,
  trackCommandUse,
} from './recents';
export type { RecentEntry, RecentKind, RecentsScope } from './recents';
