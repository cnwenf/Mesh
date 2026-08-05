/**
 * 标签与自定义字段功能入口(label-property.md:定义层 + issue 关联层)。
 */
export { LabelsPanel } from './LabelsPanel';
export { CustomFieldsPanel } from './CustomFieldsPanel';
export { ColorPicker, isValidHexColor, PRESET_COLORS } from './ColorPicker';
export { LabelDots } from './LabelDots';
export { WorkspaceLabelsPage } from './pages/WorkspaceLabelsPage';
export { WorkspaceCustomFieldsPage } from './pages/WorkspaceCustomFieldsPage';
export { IssueLabelsEditor } from './IssueLabelsEditor';
export { IssueCustomFieldsEditor } from './IssueCustomFieldsEditor';
export * from './api';
export * from './associationApi';
export type {
  CustomFieldValue,
  FieldValueInput,
  FieldValueListingEntry,
  FieldValueMemberRef,
  IssueLabelsPayload,
  MergeLabelResult,
} from './associationTypes';
export type { CompactLabel } from './types';
