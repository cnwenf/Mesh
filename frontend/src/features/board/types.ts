/**
 * 看板视图类型(kanban.md §2.2/§2.3 — 视图 = JSONB 投影配置)。
 * 字段一律 snake_case(与后端信封逐字对齐);本模块为「定义层」静态切片:
 * 只持久化「如何投影」,不持有 issue 集合(投影查询属 issue 耦合增量)。
 */

export type ViewLayout = 'board' | 'list' | 'timeline' | 'table';
export type ViewVisibility = 'private' | 'shared';
export type WipEnforcement = 'warn' | 'block';

/** 内置分组字段与以 UUID 标识的自定义字段轴(kanban §2.4)。 */
export type BuiltinGroupByField =
  'state_category' | 'status' | 'assignee' | 'priority' | 'project' | 'label';
export type CustomFieldGroupBy = `${string}-${string}-${string}-${string}-${string}`;
export type GroupByField = BuiltinGroupByField | CustomFieldGroupBy;

export type FilterOperator = 'AND' | 'OR';

export type FilterOp =
  | 'eq'
  | 'neq'
  | 'in'
  | 'not_in'
  | 'lt'
  | 'lte'
  | 'gt'
  | 'gte'
  | 'is_null'
  | 'is_not_null'
  | 'contains';

/** 内置字段条件(kanban §2.3)。 */
export interface FieldCondition {
  readonly field: string;
  readonly op: FilterOp;
  readonly value?: unknown;
}

/** 自定义字段条件(field_def_id 指向 label-property 字段定义)。 */
export interface CustomFieldCondition {
  readonly field_kind: 'custom_field';
  readonly field_def_id: string;
  readonly op: FilterOp;
  readonly value?: unknown;
}

export type FilterCondition = FieldCondition | CustomFieldCondition | FilterGroup;

export interface FilterGroup {
  readonly operator: FilterOperator;
  readonly conditions: readonly FilterCondition[];
}

/** 顶层 filters:{} = 不过滤;否则为组合条件组。 */
export type Filters = FilterGroup | Record<string, never>;

export interface SortRule {
  readonly field?: string;
  readonly field_kind?: 'custom_field';
  readonly field_def_id?: string;
  readonly order: 'asc' | 'desc';
}

export interface WipLimit {
  readonly limit: number;
  readonly enforcement: WipEnforcement;
}

export interface BoardSettings {
  /** 列序;group_by=state_category 时为 category 值 */
  readonly columns?: readonly string[];
  readonly collapsed_columns?: readonly string[];
  readonly card_fields?: readonly string[];
  /** 列 key → WIP 限制(kanban §2.5 内嵌方案) */
  readonly wip?: Readonly<Record<string, WipLimit>>;
}

/** 保存的视图(§2.2 views 表的 API 呈现)。 */
export interface View {
  readonly id: string;
  readonly workspace_id: string;
  readonly project_id: string | null;
  readonly owner_member_id: string;
  readonly name: string;
  readonly layout: ViewLayout;
  readonly visibility: ViewVisibility;
  readonly filters: Filters;
  readonly group_by: GroupByField | null;
  readonly sub_group_by: GroupByField | null;
  readonly sort: readonly SortRule[];
  readonly display_fields: readonly string[];
  readonly board_settings: BoardSettings;
  readonly position: number;
  readonly is_default: boolean;
  readonly created_at: string;
  readonly updated_at: string;
  /** 服务端按请求者计算的写权限快照 */
  readonly can_write?: boolean;
}

export interface CreateViewBody {
  readonly name: string;
  readonly layout?: ViewLayout;
  readonly visibility?: ViewVisibility;
  readonly project_id?: string | null;
  readonly filters?: Filters;
  readonly group_by?: GroupByField | null;
  readonly sub_group_by?: GroupByField | null;
  readonly sort?: readonly SortRule[];
  readonly display_fields?: readonly string[];
  readonly board_settings?: BoardSettings;
  readonly is_default?: boolean;
}

/** PATCH /views/{id} 请求体(全可选,三态由字段在场性表达)。 */
export type UpdateViewBody = Partial<CreateViewBody>;

export interface WipBody {
  readonly group_key: string;
  /** null = 移除该列 WIP 规则 */
  readonly limit: number | null;
  readonly enforcement?: WipEnforcement;
}

/** 列描述(由视图配置派生,kanban §2.4 映射表)。 */
export interface BoardColumn {
  readonly key: string;
  /** i18n 消息键或字面标签 */
  readonly label: string;
  readonly collapsed: boolean;
  readonly wip: WipLimit | null;
  /** 该列当前卡片数;定义层切片恒为 0(不接真实 issue 数据) */
  readonly count: number;
  /** 数据源为动态实体(status/assignee/project/label)时,本切片无列数据 */
  readonly placeholder: boolean;
}
