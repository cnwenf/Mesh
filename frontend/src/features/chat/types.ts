/**
 * 聊天模块实体类型(chat-session.md §2 / §3,README §6.8 流式协议)。
 * 字段一律 snake_case(与后端信封逐字对齐);本地 UI 状态另用 camelCase。
 * 流式事件经 fetch + ReadableStream 消费(非原生 EventSource,§6.8 选项 4),
 * 其线缆帧形态见 sse.ts;本文件给出解析后的判别联合 StreamEvent。
 */

/** 会话状态机(chat-session.md §2.1):active 可收发,archived 只读,deleted 软删。 */
export type ChatSessionStatus = 'active' | 'archived' | 'deleted';

/** 消息角色(§2.2):user 人类发起 / agent 智能体回复 / system 系统提示。 */
export type ChatRole = 'user' | 'agent' | 'system';

/** 生成状态(§3.3 流内事件推导):streaming 进行中 / done 完成 / failed 失败 / interrupted 中断。 */
export type GenerationStatus = 'streaming' | 'done' | 'failed' | 'interrupted';

/** 会话上的 agent 快照(真源 agents;此处仅展示所需子集,§2.1)。 */
export interface ChatAgentRef {
  readonly id: string;
  readonly name: string;
  readonly avatar_url: string | null;
}

/** 会话对象(§2.1 / §3.1)。置顶真源为 favorites(§6.19);pinned 仅请求方快照。 */
export interface ChatSession {
  readonly id: string;
  readonly workspace_id: string;
  readonly owner_id: string;
  readonly agent_id: string;
  readonly agent: ChatAgentRef;
  readonly title: string;
  readonly title_is_auto: boolean;
  readonly context_issue_id: string | null;
  readonly context_project_id: string | null;
  readonly status: ChatSessionStatus;
  readonly pinned: boolean;
  readonly last_message_at: string | null;
  readonly last_message_preview: string | null;
  readonly message_count: number;
  readonly created_at: string;
  readonly updated_at: string;
}

/** 消息附件引用(§2.4 引用 attachment 模块;此处为消息内联快照)。 */
export interface ChatAttachmentRef {
  readonly id: string;
  readonly file_name: string;
  readonly mime_type: string | null;
  readonly byte_size: number;
  readonly scan_status: string;
}

/** 消息对象(§2.2)。候选分支经 parent_id 关联,selected_candidate 标记选中项。 */
export interface ChatMessage {
  readonly id: string;
  readonly session_id: string;
  readonly role: ChatRole;
  readonly content: string;
  readonly generation_id: string | null;
  readonly generation_status: GenerationStatus;
  readonly parent_id: string | null;
  readonly selected_candidate: boolean;
  readonly quote_message_id: string | null;
  readonly prompt_tokens: number | null;
  readonly completion_tokens: number | null;
  readonly error_message: string | null;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly created_at: string;
  readonly attachments: readonly ChatAttachmentRef[];
  /** 候选总数(仅 parent_id 查询返回);非候选场景为 null。 */
  readonly candidate_count: number | null;
  readonly candidate_index: number | null;
}

/* ---- 流式事件(§3.3 / README §6.7 流内事件注册表) ---- */

/** SSE 线缆帧(sse.ts 解析 `id:/event:/data:` 后的中间形态;data 为原始 JSON 文本)。 */
export interface SseFrame {
  readonly id: string | null;
  readonly event: string;
  readonly data: string;
}

/** 解析后的流式事件判别联合(§6.7)。ping 仅心跳,UI 不消费。 */
export type StreamEvent =
  | { readonly type: 'message.created'; readonly message_id: string; readonly role: ChatRole; readonly generation_status: GenerationStatus }
  | { readonly type: 'message.delta'; readonly message_id: string; readonly delta: string }
  | { readonly type: 'message.done'; readonly message_id: string; readonly generation_status: GenerationStatus; readonly completion_tokens: number | null }
  | { readonly type: 'message.interrupted'; readonly message_id: string; readonly partial_content: string; readonly generation_status: GenerationStatus }
  | { readonly type: 'error'; readonly message_id: string | null; readonly code: string; readonly message: string }
  | { readonly type: 'ping'; readonly ts: string | null };

/** 终态事件集合:命中后不再自动重连(§6.8 断点续传语义)。 */
export const TERMINAL_STREAM_EVENTS: ReadonlySet<string> = new Set([
  'message.done',
  'message.interrupted',
  'error',
]);

/* ---- 写操作请求/响应(§3.1–§3.5) ---- */

export interface CreateChatSessionBody {
  readonly agent_id: string;
  readonly context_issue_id?: string | null;
  readonly context_project_id?: string | null;
  readonly title?: string;
}

export interface PatchChatSessionBody {
  readonly title?: string;
  readonly status?: 'active' | 'archived';
  readonly context_issue_id?: string | null;
  readonly context_project_id?: string | null;
}

export interface ListChatSessionsParams {
  readonly agent_id?: string;
  readonly status?: 'active' | 'archived';
  readonly limit?: number;
  readonly cursor?: string;
}

export interface ListChatMessagesParams {
  readonly limit?: number;
  readonly cursor?: string;
  readonly parent_id?: string;
}

export interface SendMessageBody {
  readonly content: string;
  readonly attachment_ids?: readonly string[];
  readonly quote_message_id?: string | null;
}

/** POST messages / regenerate 的统一响应(§3.3):返回流入口 stream_url。 */
export interface GenerationStartResult {
  readonly message_id: string;
  readonly generation_id: string;
  readonly stream_url: string;
}

/** POST stop 响应(§3.3 幂等中断)。 */
export interface StopGenerationResult {
  readonly generation_id: string;
  readonly message_id: string;
  readonly generation_status: GenerationStatus;
}

/** POST select 响应(§3.2 候选选中)。 */
export interface SelectCandidateResult {
  readonly parent_id: string;
  readonly selected_message_id: string;
}

/* ---- 沉淀为评论(§4 沉淀 / README §6.9 触发矩阵) ---- */

export interface DistillTriggeredAgent {
  readonly member_id: string;
  readonly agent_id: string;
  readonly name: string;
}

export interface DistillMention {
  readonly id: string;
  readonly member_type: 'human' | 'agent';
  readonly name: string;
}

export interface DistillTargetIssue {
  readonly id: string;
  readonly identifier: string;
  readonly title: string;
}

/** distill-preview 响应(§6.9 副作用预览:目标 issue + 最终正文 + 触发 agent 名单)。 */
export interface DistillPreview {
  readonly target_issue: DistillTargetIssue;
  readonly body_markdown: string;
  readonly attachments: readonly ChatAttachmentRef[];
  readonly triggered_agents: readonly DistillTriggeredAgent[];
  readonly mentions: readonly DistillMention[];
  readonly can_trigger_agents: boolean;
  readonly suppress_triggers_supported: boolean;
}

export interface DistillPreviewBody {
  readonly body_markdown: string;
  readonly target_issue_id?: string | null;
  readonly attachment_ids?: readonly string[];
}
