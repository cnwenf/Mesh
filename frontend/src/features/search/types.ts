/**
 * 全局搜索端点结果类型(契约:docs/specs/features/search-command-palette.md §3.2)。
 *
 * - 服务端只返回稳定 key + 结构化数据:本地化副标题/徽章文案由前端经消息目录组装(§6.18);
 * - `highlight` 只返回原始 title 上的 Unicode code point 偏移区间(半开 [start,end)),
 *   绝不返回 HTML;前端经 Array.from(title) 映射渲染(§3.2);
 * - `badge.color` 仅取语义 token 名(status/danger/warn/success/info),前端映射 token 类。
 */

/** 六类搜索对象(§3.2 type 枚举,闭合) */
export type SearchResultType = 'issue' | 'member' | 'agent' | 'project' | 'view' | 'chat_session';

/** issue 结构化上下文:identifier + 所属项目(可空)+ 状态(枚举走 category 稳定 key) */
export interface SearchIssueContext {
  readonly identifier: string;
  readonly project: { readonly id: string; readonly name: string } | null;
  readonly status: { readonly id: string; readonly name: string; readonly category: string };
}

/** agent 容量快照(§6.12「运行中 N / 排队 M / 需审批 K」;服务端快照,不保证实时) */
export interface AgentCapacity {
  readonly running: number;
  readonly queued: number;
  readonly awaiting_approval: number;
}

/** member / agent 结构化上下文(同源 members 表;capacity 仅 agent 携带) */
export interface SearchMemberContext {
  readonly member_type: 'human' | 'agent';
  readonly role: string;
  readonly capacity?: AgentCapacity;
}

/** project 结构化上下文(visibility 为稳定 key,文案经消息目录) */
export interface SearchProjectContext {
  readonly visibility: string;
  readonly key: string;
}

/** view 结构化上下文(scope 稳定 key;归属项目可选;owner_only 私有视图标记) */
export interface SearchViewContext {
  readonly scope: 'project' | 'workspace';
  readonly project?: { readonly id: string; readonly name: string };
  readonly owner_only?: boolean;
}

/** chat_session 结构化上下文(参与者计数 + 可选 agent) */
export interface SearchChatSessionContext {
  readonly participants_count: number;
  readonly agent?: { readonly id: string; readonly name: string };
}

/** 按类型判别的上下文联合(前端据 type 窄化取字段) */
export type SearchContext =
  | SearchIssueContext
  | SearchMemberContext
  | SearchProjectContext
  | SearchViewContext
  | SearchChatSessionContext;

/** 徽章语义色名(仅这五个,映射语义 token 类;theme.md) */
export type BadgeColor = 'status' | 'danger' | 'warn' | 'success' | 'info';

/** 徽章:文案 = 消息目录 key + 参数(服务端不返回拼接好的可见句子,§6.18) */
export interface SearchBadge {
  readonly kind: string;
  readonly label_key: string;
  readonly label_params: Readonly<Record<string, string | number>>;
  readonly color: BadgeColor;
}

/** 命中标注:offset 单位为原始 title 的 Unicode code point,半开区间 [start,end) */
export interface SearchHighlight {
  readonly title?: {
    readonly unit: 'codepoint';
    readonly ranges: ReadonlyArray<readonly [number, number]>;
  };
}

interface SearchResultBase {
  readonly id: string;
  /** 主标题原文(未归一化;供渲染与 highlight 映射) */
  readonly title: string;
  /** 类型图标键(语义键,前端映射字形) */
  readonly icon: string;
  /** 规范深链(§3.4),Enter 直达 */
  readonly url: string;
  readonly badge?: SearchBadge;
  readonly highlight?: SearchHighlight;
}

/** 结果条目统一形状(按 type 判别,§3.2) */
export type SearchResultItem =
  | (SearchResultBase & { readonly type: 'issue'; readonly context: SearchIssueContext })
  | (SearchResultBase & { readonly type: 'member'; readonly context: SearchMemberContext })
  | (SearchResultBase & { readonly type: 'agent'; readonly context: SearchMemberContext })
  | (SearchResultBase & { readonly type: 'project'; readonly context: SearchProjectContext })
  | (SearchResultBase & { readonly type: 'view'; readonly context: SearchViewContext })
  | (SearchResultBase & { readonly type: 'chat_session'; readonly context: SearchChatSessionContext });

/** 收藏条目(§6.19 GET /api/v1/favorites 包络元素) */
export interface FavoriteEntry {
  readonly id: string;
  readonly workspace_id: string;
  readonly member_id: string;
  readonly target_type: SearchResultType;
  readonly target_id: string;
  readonly created_at: string;
}
