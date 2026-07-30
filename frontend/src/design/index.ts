/**
 * 设计系统公共 API(README §6.12)。
 * 语义 token 样式经 `design/base.css` 在应用根部引入(勿直接 import tokens.css)。
 */
export { contrastRatio, hexToRgb, meetsAA, relativeLuminance, WCAG_AA_RATIO } from './contrast';
export type { Rgb } from './contrast';
export { AA_CONTRAST_PAIRS, DARK_TOKENS, LIGHT_TOKENS } from './tokenValues';
export { ThemeProvider, resolveTheme } from './ThemeProvider';
export { ThemeSkeleton } from './ThemeSkeleton';
export {
  resolveThemeChain,
  expectedRouteId,
  parseThemeLocator,
  isThemeMode,
} from './themeNegotiation';
export type {
  ResolvedTheme,
  ThemeSource,
  ThemeMode,
  ChainInput,
  ChainResult,
} from './themeNegotiation';
export { THEME_LOCATOR_KEY, writeThemeLocator, clearThemeLocators } from './themeLocator';
export { guardUgcInlineColors, useUgcColorGuard, THEME_CHANGED_EVENT } from './ugcColorGuard';
export { Banner } from './components/Banner';
export type { BannerProps, BannerTone } from './components/Banner';
export { Button, buttonClasses } from './components/Button';
export type { ButtonProps, ButtonSize, ButtonVariant } from './components/Button';
export { Checkbox } from './components/Checkbox';
export type { CheckboxProps } from './components/Checkbox';
export { DataTable } from './components/DataTable';
export type { DataTableColumn, DataTableProps, DataTableSortState } from './components/DataTable';
export { Dialog } from './components/Dialog';
export type { DialogProps } from './components/Dialog';
export { EmptyState } from './components/EmptyState';
export type { EmptyStateProps } from './components/EmptyState';
export { ErrorState } from './components/ErrorState';
export type { ErrorStateProps } from './components/ErrorState';
export { Field } from './components/Field';
export type { FieldControlProps, FieldProps, FieldRenderArgument } from './components/Field';
export { PageHeader } from './components/PageHeader';
export type { PageHeaderProps } from './components/PageHeader';
export { Popover } from './components/Popover';
export type { PopoverProps } from './components/Popover';
export { Switch } from './components/Switch';
export type { SwitchProps } from './components/Switch';
export { Textarea, TEXTAREA_MAX_HEIGHT_PX } from './components/Textarea';
export type { TextareaProps } from './components/Textarea';
export { Toolbar } from './components/Toolbar';
export type { ToolbarProps } from './components/Toolbar';
export { IconButton } from './components/IconButton';
export type { IconButtonProps } from './components/IconButton';
export { Input } from './components/Input';
export type { InputProps, InputSize } from './components/Input';
export { Kbd } from './components/Kbd';
export type { KbdProps } from './components/Kbd';
export { Select } from './components/Select';
export type { SelectProps } from './components/Select';
export { Skeleton } from './components/Skeleton';
export type { SkeletonProps } from './components/Skeleton';
export { StatusDot } from './components/StatusDot';
export type { StatusDotProps, StatusDotTone } from './components/StatusDot';
export { DEFAULT_TOAST_DURATION_MS, ToastProvider, useToast } from './components/Toast';
export type {
  ToastContextValue,
  ToastItem,
  ToastOptions,
  ToastProviderProps,
  ToastTimer,
  ToastTone,
} from './components/Toast';
export { Accordion } from './components/Accordion';
export type { AccordionItem, AccordionProps } from './components/Accordion';
export { Avatar, avatarHueIndex, avatarInitials } from './components/Avatar';
export type { AvatarProps, AvatarSize } from './components/Avatar';
export { Badge, BADGE_TONE_ICONS } from './components/Badge';
export type { BadgeProps, BadgeSize, BadgeTone } from './components/Badge';
export { Drawer } from './components/Drawer';
export type { DrawerProps } from './components/Drawer';
export { Icon, ICON_PATHS } from './components/Icon';
export type { IconName, IconProps, IconSize } from './components/Icon';
export { Menu } from './components/Menu';
export type { MenuItem, MenuEntry, MenuProps } from './components/Menu';
export { PublicFlowShell } from './components/PublicFlowShell';
export type { PublicFlowShellProps } from './components/PublicFlowShell';
export { Tabs } from './components/Tabs';
export type { TabItem, TabsProps } from './components/Tabs';
export { Tooltip } from './components/Tooltip';
export type { TooltipProps } from './components/Tooltip';
export { focusableElements, trapTabKey, useFocusTrap } from './components/useFocusTrap';
