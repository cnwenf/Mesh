/**
 * 页面模板层桶导出(design-quality.md §11.1 patterns 层)。
 * features 经 `../../design` 统一桶消费,不深路径导入本目录。
 */
export { DataView } from './DataView';
export type { DataViewProps, PageHeaderCrumb } from './DataView';
export { DetailLayout } from './DetailLayout';
export type { DetailLayoutProps } from './DetailLayout';
export { FilterChips } from './FilterChips';
export type { FilterChip, FilterChipsProps } from './FilterChips';
export { BulkBar } from './BulkBar';
export type { BulkBarProps } from './BulkBar';
export { useListKeyboardSelection } from './useListKeyboardSelection';
export type {
  ListKeyboardSelection,
  UseListKeyboardSelectionOptions,
} from './useListKeyboardSelection';
