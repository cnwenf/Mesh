/**
 * 聊天模块 API 调用(契约层,chat-session.md §3.1–§3.5 / README §6.14 包络)。
 * 列表走 `list`(自动解 {data,next_cursor}),单对象走 `request`(解 {data});
 * 会话 PATCH 经 RequestOptions.ifMatch 携带乐观锁(If-Match: <updated_at>,§6.14)。
 * 置顶经 favorites 唯一真源(§6.19):PUT/DELETE /favorites/chat_session/{id} 幂等。
 * 流式端点(stream_url)不经本层 —— 见 sse.ts(fetch + ReadableStream,§6.8 选项 4)。
 */
import type { MeshApiClient } from '../../api';
import type {
  ChatMessage,
  ChatSession,
  CreateChatSessionBody,
  DistillPreview,
  DistillPreviewBody,
  GenerationStartResult,
  ListChatMessagesParams,
  ListChatSessionsParams,
  PatchChatSessionBody,
  SelectCandidateResult,
  SendMessageBody,
  StopGenerationResult,
} from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

const workspaceSessionsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/chat-sessions`;

const sessionPath = (workspaceId: string, sessionId: string): string =>
  `${workspaceSessionsPath(workspaceId)}/${sessionId}`;

const favoritePath = (sessionId: string): string =>
  `/api/v1/favorites/chat_session/${sessionId}`;

/** 会话级实时频道(chat-session.md §3.6):该会话的终态 message.* 事件。 */
export function chatSessionChannel(sessionId: string): string {
  return `chat_session:${sessionId}`;
}

/** 列表级实时频道(§3.6, H1):owner 私有频道,仅承载本人会话的列表预览更新。
 *  不再使用 workspace 级广播(那会向工作区全体成员泄漏他人私有会话终态事件)。 */
export function chatListChannel(ownerMemberId: string): string {
  return `chat_list:${ownerMemberId}`;
}

/** 创建会话(§3.1);title 缺省时服务端自动生成(title_is_auto=true)。 */
export async function createChatSession(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateChatSessionBody,
): Promise<ChatSession> {
  return client.request<ChatSession>('POST', workspaceSessionsPath(workspaceId), { body });
}

/** 会话列表(§3.2:置顶优先,其后 last_message_at 倒序;游标分页)。 */
export async function listChatSessions(
  client: MeshApiClient,
  workspaceId: string,
  params: ListChatSessionsParams = {},
): Promise<Page<ChatSession>> {
  const envelope = await client.list<ChatSession>(workspaceSessionsPath(workspaceId), {
    query: {
      agent_id: params.agent_id,
      status: params.status,
      limit: params.limit,
      cursor: params.cursor,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 取单个会话。 */
export async function getChatSession(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
): Promise<ChatSession> {
  return client.request<ChatSession>('GET', sessionPath(workspaceId, sessionId));
}

/** PATCH 更新会话(标题 / 归档 / 上下文;乐观锁 If-Match: <updated_at>,§6.14)。 */
export async function patchChatSession(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
  body: PatchChatSessionBody,
  ifMatch?: string,
): Promise<ChatSession> {
  return client.request<ChatSession>('PATCH', sessionPath(workspaceId, sessionId), {
    body,
    ifMatch,
  });
}

/** 删除会话(§3.1 软删除 → status='deleted';204 无体)。 */
export async function deleteChatSession(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
): Promise<void> {
  await client.request<void>('DELETE', sessionPath(workspaceId, sessionId));
}

/** 消息列表(§3.2 时间倒序;带 parent_id 时返回该父的全部候选,时间正序 + candidate_*)。 */
export async function listChatMessages(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
  params: ListChatMessagesParams = {},
): Promise<Page<ChatMessage>> {
  const envelope = await client.list<ChatMessage>(
    `${sessionPath(workspaceId, sessionId)}/messages`,
    {
      query: { limit: params.limit, cursor: params.cursor, parent_id: params.parent_id },
    },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/**
 * 发送消息(§3.3):入队 agent 生成并返回流入口 stream_url。
 * 错误码:409 generation_in_progress / 422 session_not_active / 429 rate_limited。
 * attachment_ids 由后端在发送时关联(§2.4;附件先经 attachment 模块直传取得 id)。
 */
export async function sendMessage(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
  body: SendMessageBody,
): Promise<GenerationStartResult> {
  return client.request<GenerationStartResult>(
    'POST',
    `${sessionPath(workspaceId, sessionId)}/messages`,
    { body },
  );
}

/** 重新生成(§3.3):对某 agent 消息再生成一个候选兄弟分支,返回新流入口。 */
export async function regenerateMessage(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
  messageId: string,
): Promise<GenerationStartResult> {
  return client.request<GenerationStartResult>(
    'POST',
    `${sessionPath(workspaceId, sessionId)}/messages/${messageId}/regenerate`,
  );
}

/** 选中候选(§3.2):将 parent 的选中项切到 selected_message_id。 */
export async function selectCandidate(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
  messageId: string,
  selectedMessageId: string,
): Promise<SelectCandidateResult> {
  return client.request<SelectCandidateResult>(
    'POST',
    `${sessionPath(workspaceId, sessionId)}/messages/${messageId}/select`,
    { body: { selected_message_id: selectedMessageId } },
  );
}

/** 中断生成(§3.3 独立幂等端点;重复 stop 幂等,202)。 */
export async function stopGeneration(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
  generationId: string,
): Promise<StopGenerationResult> {
  return client.request<StopGenerationResult>(
    'POST',
    `${sessionPath(workspaceId, sessionId)}/generations/${generationId}/stop`,
  );
}

/** 沉淀预览(§6.9 副作用预览):返回目标 issue + 最终正文 + 触发 agent 名单。 */
export async function distillPreview(
  client: MeshApiClient,
  workspaceId: string,
  sessionId: string,
  body: DistillPreviewBody,
): Promise<DistillPreview> {
  return client.request<DistillPreview>(
    'POST',
    `${sessionPath(workspaceId, sessionId)}/distill-preview`,
    { body },
  );
}

/** 置顶会话(§6.19 favorites 唯一真源;PUT 幂等,201)。 */
export async function putSessionFavorite(
  client: MeshApiClient,
  sessionId: string,
): Promise<void> {
  await client.request<void>('PUT', favoritePath(sessionId));
}

/** 取消置顶(§6.19;DELETE 幂等,204)。 */
export async function deleteSessionFavorite(
  client: MeshApiClient,
  sessionId: string,
): Promise<void> {
  await client.request<void>('DELETE', favoritePath(sessionId));
}

/** 收藏条目(§6.19):用于校正会话列表的 pinned 真源。 */
export interface FavoriteEntry {
  readonly target_type: string;
  readonly target_id: string;
}

export async function listSessionFavorites(
  client: MeshApiClient,
  workspaceId: string,
): Promise<readonly FavoriteEntry[]> {
  const envelope = await client.list<FavoriteEntry>('/api/v1/favorites', {
    query: { workspace_id: workspaceId, target_type: 'chat_session' },
  });
  return envelope.data;
}
