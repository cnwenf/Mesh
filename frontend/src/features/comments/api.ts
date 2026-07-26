/**
 * 评论模块 API 调用(契约层,comment-inbox.md §3.1 / README §6.14 包络)。
 * 列表走 `list`(自动解 {data,next_cursor}),单对象走 `request`;
 * 编辑经 RequestOptions.ifMatch 携带乐观锁(If-Match: <updated_at>,409 收敛)。
 * issue 作用域路径由服务端解析 workspace(无需客户端传 workspace_id)。
 */
import type { MeshApiClient } from '../../api';
import type { Comment, CreateCommentBody, ListCommentsParams, ReactionSummary } from './types';

export interface Page<T> {
  readonly data: readonly T[];
  readonly nextCursor: string | null;
}

const issueCommentsPath = (issueId: string): string => `/api/v1/issues/${issueId}/comments`;
const commentPath = (commentId: string): string => `/api/v1/comments/${commentId}`;

/** 评论级实时频道:复用 issue:{id}(comment.* / reaction.* / execution.* 事件同频道)。 */
export function issueChannel(issueId: string): string {
  return `issue:${issueId}`;
}

/** 列出评论(默认顶层 + reply_count + 预览回复;include=none 则不带预览)。 */
export async function listComments(
  client: MeshApiClient,
  issueId: string,
  params: ListCommentsParams = {},
): Promise<Page<Comment>> {
  const envelope = await client.list<Comment>(issueCommentsPath(issueId), {
    query: {
      limit: params.limit,
      cursor: params.cursor,
      include: params.include,
      order: params.order,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 发表评论(可带 parent_id 成为回复;Idempotency-Key 由客户端自动携带)。 */
export async function createComment(
  client: MeshApiClient,
  issueId: string,
  body: CreateCommentBody,
): Promise<Comment> {
  return client.request<Comment>('POST', issueCommentsPath(issueId), { body });
}

/** 取单条评论。 */
export async function getComment(client: MeshApiClient, commentId: string): Promise<Comment> {
  return client.request<Comment>('GET', commentPath(commentId));
}

/** 编辑评论(乐观锁:If-Match: <updated_at>;409 conflict 表示版本过期)。 */
export async function updateComment(
  client: MeshApiClient,
  commentId: string,
  bodyMarkdown: string,
  ifMatch: string,
): Promise<Comment> {
  return client.request<Comment>('PATCH', commentPath(commentId), {
    body: { body_markdown: bodyMarkdown },
    ifMatch,
  });
}

/** 软删除评论(留占位保线程完整)。 */
export async function deleteComment(client: MeshApiClient, commentId: string): Promise<void> {
  await client.request<void>('DELETE', commentPath(commentId));
}

/** 列出某线程回复(游标分页,时间正序)。 */
export async function listReplies(
  client: MeshApiClient,
  commentId: string,
  params: { limit?: number; cursor?: string } = {},
): Promise<Page<Comment>> {
  const envelope = await client.list<Comment>(`${commentPath(commentId)}/replies`, {
    query: { limit: params.limit, cursor: params.cursor },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 解决线程(仅顶层)。 */
export async function resolveThread(client: MeshApiClient, commentId: string): Promise<Comment> {
  return client.request<Comment>('POST', `${commentPath(commentId)}/resolve`);
}

/** 重开线程(仅顶层)。 */
export async function reopenThread(client: MeshApiClient, commentId: string): Promise<Comment> {
  return client.request<Comment>('POST', `${commentPath(commentId)}/reopen`);
}

/** 列出评论反应聚合。 */
export async function listReactions(
  client: MeshApiClient,
  commentId: string,
): Promise<readonly ReactionSummary[]> {
  const envelope = await client.list<ReactionSummary>(`${commentPath(commentId)}/reactions`);
  return envelope.data;
}

/** 添加反应(自己的);重复 → 409 conflict。返回最新聚合。 */
export async function addReaction(
  client: MeshApiClient,
  commentId: string,
  emoji: string,
): Promise<readonly ReactionSummary[]> {
  const envelope = await client.request<readonly ReactionSummary[]>(
    'POST',
    `${commentPath(commentId)}/reactions`,
    { body: { emoji } },
  );
  return envelope;
}

/** 取消(自己的)反应。 */
export async function removeReaction(
  client: MeshApiClient,
  commentId: string,
  emoji: string,
): Promise<void> {
  await client.request<void>(
    'DELETE',
    `${commentPath(commentId)}/reactions/${encodeURIComponent(emoji)}`,
  );
}
