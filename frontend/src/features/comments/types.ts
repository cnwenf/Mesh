/**
 * 评论模块实体类型(comment-inbox.md §2.2 / §3.1)。
 * 字段一律 snake_case(与后端信封逐字对齐);人类/agent 判别以服务端
 * `author.member_type` / `mentions[].member_type` 快照为准(真源 members,README §6.1)。
 */

export type AuthorKind = 'member' | 'system';
export type MemberType = 'human' | 'agent';
export type MemberStatus = 'active' | 'disabled' | 'removed';
export type CommentDeliveryState = 'sending' | 'failed' | 'sent';

/** 轻量成员引用(作者 / 提及 / 反应人):服务端解析显示名 + 类型快照。 */
export interface CommentMemberRef {
  readonly id: string;
  readonly member_type: MemberType;
  readonly name: string;
  /** 作者历史展示需要区分已移除的 agent；旧服务响应可暂不携带。 */
  readonly status?: MemberStatus;
}

/** 表情回应聚合(评论级):emoji + 计数 + 当前用户是否已反应 + 反应人列表。 */
export interface ReactionSummary {
  readonly emoji: string;
  readonly count: number;
  readonly reacted_by_me: boolean;
  readonly actors: readonly CommentMemberRef[];
}

export interface Comment {
  readonly id: string;
  readonly issue_id: string;
  readonly parent_id: string | null;
  readonly thread_root_id: string | null;
  readonly author_kind: AuthorKind;
  readonly author: CommentMemberRef | null;
  readonly body_markdown: string;
  /** 服务端净化后的 HTML(白名单防 XSS);仅此字段可经 dangerouslySetInnerHTML 渲染。 */
  readonly body_html: string;
  readonly body_text: string;
  readonly reactions: readonly ReactionSummary[];
  readonly reply_count: number;
  readonly resolved_at: string | null;
  readonly resolved_by: CommentMemberRef | null;
  readonly mentions: readonly CommentMemberRef[];
  readonly triggered_execution_ids: readonly string[];
  readonly deleted_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly edited_at: string | null;
  /** 仅客户端乐观实体使用；服务端实体缺省等价于 sent。 */
  readonly delivery_state?: CommentDeliveryState;
  /** UUID 请求关联键；服务端实时回显，用于 HTTP/WS 竞态对账及失败重试。 */
  readonly client_request_id?: string | null;
  /** 仅客户端保留，用于失败实体的等价重试匹配。 */
  readonly suppress_triggers?: boolean;
  /** 列表(include=replies)附带的前 N 条预览回复;详情/单条无此字段。 */
  readonly preview_replies?: readonly Comment[];
}

/** 发表评论请求体(comment-inbox.md §3.1)。提及由服务端从 Markdown 解析为准。 */
export interface CreateCommentBody {
  readonly body_markdown: string;
  readonly parent_id?: string | null;
  /** 当前阶段附件必须为空(后端约束)。 */
  readonly attachment_ids?: readonly string[];
  /** true = 仅通知不运行(README §6.9 显式抑制)。 */
  readonly suppress_triggers?: boolean;
}

export interface ListCommentsParams {
  readonly limit?: number;
  readonly cursor?: string;
  readonly include?: 'replies' | 'none';
  readonly order?: 'asc' | 'desc';
}

/**
 * 评论占位恢复所需的最小 execution REST 投影。完整执行详情属于 runtime 模块；
 * 评论区只依赖这些稳定字段，避免复制或猜测 attempt/runtime 数据。
 */
export interface CommentExecutionSnapshot {
  readonly id: string;
  readonly issue_id: string | null;
  readonly agent_id: string | null;
  readonly status: string;
  readonly failure_reason: string | null;
}

/** 反应人运行时结构守卫(边界处校验,不信任外部载荷)。 */
export function isCommentMemberRef(value: unknown): value is CommentMemberRef {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.name === 'string' &&
    (candidate.member_type === 'human' || candidate.member_type === 'agent')
  );
}

/** Comment 运行时结构守卫(realtime 帧合并前校验,不信任 WS 载荷)。 */
export function isComment(value: unknown): value is Comment {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.issue_id === 'string' &&
    typeof candidate.body_markdown === 'string' &&
    typeof candidate.created_at === 'string'
  );
}

/** 已删除评论:deleted_at 非空(渲染「该评论已删除」占位,保线程完整)。 */
export function isDeletedComment(comment: Comment): boolean {
  return comment.deleted_at !== null;
}

/** 线程是否已解决(仅顶层有意义)。 */
export function isResolved(comment: Comment): boolean {
  return comment.resolved_at !== null;
}
