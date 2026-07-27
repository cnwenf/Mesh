/**
 * issue 关联层类型(label-property.md §3.1/§3.2 关联端点,MES-32 余量切片)。
 * 定义层类型(Label/CustomFieldDef/CustomFieldOption)见 types.ts。
 */
import type { CustomFieldDef, Label } from './types';

/** issue 标签关联端点的包络内层({@code {"data": {"labels": [...]}}}）。 */
export interface IssueLabelsPayload {
  readonly labels: readonly Label[];
}

/** 成员型字段值的成员快照(人或 agent,member.md §2.4 单一 display_name)。 */
export interface FieldValueMemberRef {
  readonly id: string;
  readonly name: string;
  readonly member_type: 'human' | 'agent';
}

/** issue 上的一个字段值(按类型仅一列非空,§2.6)。 */
export interface CustomFieldValue {
  readonly field_def_id: string;
  readonly issue_id: string;
  readonly value_text: string | null;
  readonly value_number: number | null;
  readonly value_date: string | null;
  readonly value_member_id: string | null;
  readonly value_member: FieldValueMemberRef | null;
  readonly value_boolean: boolean | null;
  readonly value_json: unknown;
  readonly created_at: string;
  readonly updated_at: string;
}

/** 字段值列表项:字段定义快照 + 当前值(§3.2 "含字段定义快照")。 */
export interface FieldValueListingEntry {
  readonly field_def: CustomFieldDef;
  readonly value: CustomFieldValue | null;
}

/**
 * PUT /issues/{id}/custom-field-values 的单条输入(§3.2)。
 * 恰好携带一个 value_* 字段;显式 null 表示清空该字段。
 */
export interface FieldValueInput {
  readonly field_def_id: string;
  readonly value_text?: string | null;
  readonly value_number?: number | null;
  readonly value_date?: string | null;
  readonly value_member_id?: string | null;
  readonly value_boolean?: boolean | null;
  readonly value_json?: unknown;
}

/** 标签合并结果(§3.2)。 */
export interface MergeLabelResult {
  readonly merged_issue_count: number;
  readonly target_label: {
    readonly id: string;
    readonly name: string;
    readonly color: string;
  };
}
