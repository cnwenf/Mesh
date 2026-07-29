/**
 * 设计系统公共 API(README §6.12)。
 * 语义 token 样式经 `design/base.css` 在应用根部引入(勿直接 import tokens.css)。
 */
export { contrastRatio, hexToRgb, meetsAA, relativeLuminance, WCAG_AA_RATIO } from './contrast';
export type { Rgb } from './contrast';
export { AA_CONTRAST_PAIRS, DARK_TOKENS, LIGHT_TOKENS } from './tokenValues';
export { ThemeProvider, resolveTheme } from './ThemeProvider';
export { ThemeSkeleton } from './ThemeSkeleton';
export { resolveThemeChain, expectedRouteId, parseThemeLocator, isThemeMode } from './themeNegotiation';
export type { ResolvedTheme, ThemeSource, ThemeMode, ChainInput, ChainResult } from './themeNegotiation';
export { THEME_LOCATOR_KEY, writeThemeLocator, clearThemeLocators } from './themeLocator';
export { Banner } from './components/Banner';
export type { BannerProps, BannerTone } from './components/Banner';
export { Button, buttonClasses } from './components/Button';
export type { ButtonProps, ButtonSize, ButtonVariant } from './components/Button';
export { Dialog } from './components/Dialog';
export type { DialogProps } from './components/Dialog';
export { EmptyState } from './components/EmptyState';
export type { EmptyStateProps } from './components/EmptyState';
export { ErrorState } from './components/ErrorState';
export type { ErrorStateProps } from './components/ErrorState';
export { IconButton } from './components/IconButton';
export type { IconButtonProps } from './components/IconButton';
export { Input } from './components/Input';
export type { InputProps } from './components/Input';
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
