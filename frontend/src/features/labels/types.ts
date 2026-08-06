/**
 * 标签与自定义字段(定义层)契约类型(label-property.md §2/§3,定义层切片)。
 * 字段与后端包络逐字对齐(snake_case,readonly);表单本地态另用 camelCase。
 * issue 关联(打标签 / 字段值 / 合并)属 MES-32 余量切片,不在本类型集内。
 */

/** §1.3 字段类型封闭清单(formula/rollup 不预留取值)。 */
export type CustomFieldType =
  | 'text'
  | 'textarea'
  | 'number'
  | 'date'
  | 'datetime'
  | 'single_select'
  | 'multi_select'
  | 'member'
  | 'boolean'
  | 'url';

export const CUSTOM_FIELD_TYPES: readonly CustomFieldType[] = [
  'text',
  'textarea',
  'number',
  'date',
  'datetime',
  'single_select',
  'multi_select',
  'member',
  'boolean',
  'url',
];

/** 枚举型字段(值取自 custom_field_options)。 */
export const SELECT_FIELD_TYPES: readonly CustomFieldType[] = ['single_select', 'multi_select'];

/** Issue 列表/看板投影里的紧凑标签快照。 */
export interface CompactLabel {
  readonly id: string;
  readonly name: string;
  readonly color: string;
}

export interface Label {
  readonly id: string;
  readonly workspace_id: string;
  readonly project_id: string | null;
  readonly name: string;
  readonly color: string;
  readonly description: string | null;
  readonly scope: 'workspace' | 'project';
  readonly created_at: string;
  readonly updated_at: string;
}

/** GET /workspaces/{ws}/labels 的设置页形态，带真实使用数。 */
export interface LabelWithUsage extends Label {
  readonly issue_count: number;
}

export interface CustomFieldOption {
  readonly id: string;
  readonly field_def_id: string;
  readonly name: string;
  readonly color: string | null;
  readonly position: number;
  readonly is_active: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CustomFieldDef {
  readonly id: string;
  readonly workspace_id: string;
  readonly project_id: string | null;
  readonly name: string;
  readonly field_key: string;
  readonly type: CustomFieldType;
  readonly is_required: boolean;
  readonly required_on: readonly string[];
  readonly default_value: unknown;
  readonly config: Readonly<Record<string, unknown>>;
  readonly position: number;
  readonly is_active: boolean;
  readonly options: readonly CustomFieldOption[];
  readonly created_at: string;
  readonly updated_at: string;
}

// --- request bodies ---------------------------------------------------------

export interface CreateLabelBody {
  readonly name: string;
  readonly color: string;
  readonly description?: string | null;
  readonly project_id?: string | null;
}

/** 三态 PATCH:省略=保持,null=清空(§6.14 乐观并发经 ifMatch)。 */
export interface UpdateLabelBody {
  readonly name?: string;
  readonly color?: string;
  readonly description?: string | null;
}

export interface OptionInput {
  readonly name: string;
  readonly color?: string | null;
  readonly position?: number;
}

export interface CreateCustomFieldBody {
  readonly name: string;
  readonly field_key: string;
  readonly type: CustomFieldType;
  readonly project_id?: string | null;
  readonly is_required?: boolean;
  readonly required_on?: readonly string[];
  readonly config?: Readonly<Record<string, unknown>>;
  readonly position?: number;
  readonly options?: readonly OptionInput[];
}

/** type / field_key 创建后不可变(稳定标识被筛选/视图引用)。 */
export interface UpdateCustomFieldBody {
  readonly name?: string;
  readonly is_required?: boolean;
  readonly required_on?: readonly string[];
  readonly default_value?: unknown;
  readonly config?: Readonly<Record<string, unknown>>;
  readonly position?: number;
  readonly is_active?: boolean;
}

export interface CreateOptionBody {
  readonly name: string;
  readonly color?: string | null;
  readonly position?: number;
}

export interface UpdateOptionBody {
  readonly name?: string;
  readonly color?: string | null;
  readonly position?: number;
  readonly is_active?: boolean;
}

export interface ListLabelsParams {
  readonly project_id?: string;
  readonly limit?: number;
  readonly cursor?: string;
}

export interface ListCustomFieldsParams {
  readonly project_id?: string;
  readonly is_active?: boolean;
  readonly limit?: number;
  readonly cursor?: string;
}
