/**
 * 单条消息气泡(chat-session.md §4.2)。user 右 / agent 左 + AI 徽标;
 * 正文经 marked + DOMPurify 本地净化渲染(聊天 content 为原始 Markdown,
 * 非评论的服务端 body_html,故复用 comments/markdown 的白名单净化路径);
 * 流式态尾随光标;failed/interrupted 呈现错误态 + 重试(重生成)入口;
 * 附件卡片 + 引用预览。纯展示 + 回调;候选分支切换见 CandidateSwitcher(父级编排)。
 */
import type { MeshApiClient } from '../../api';
import { Button, RunStateBadge } from '../../design';
import { useUgcColorGuard } from '../../design/ugcColorGuard';
import { formatRelativeTime, useT } from '../../i18n';
import { renderMarkdownPreview } from '../comments/markdown';
import { MessageAttachments } from './MessageAttachments';
import type { ChatMessage } from './types';

// 兼容既有导入路径(ChatComposer / 桶导出 / 测试):字节数格式化现居 format.ts。
export { formatByteSize } from './format';

/** 引用预览截断:过长正文取前 120 字符。 */
function truncatePreview(content: string): string {
  const trimmed = content.trim();
  return trimmed.length > 120 ? trimmed.slice(0, 120) + '…' : trimmed;
}

export interface MessageBubbleProps {
  readonly message: ChatMessage;
  readonly locale: string;
  /** 附件下载/缩略图用;缺省时放行附件退化为名称 + 大小(纯展示)。 */
  readonly client?: MeshApiClient;
  /** 引用到的原消息(父级按 quote_message_id 解析;未解析为 null)。 */
  readonly quotedMessage?: ChatMessage | null;
  /** 重生成 / 失败重试入口(仅 agent 消息呈现)。 */
  readonly onRegenerate?: (message: ChatMessage) => void;
  /** 引用此消息(写入 composer 的 quote_message_id)。 */
  readonly onQuote?: (message: ChatMessage) => void;
}

export function MessageBubble(props: MessageBubbleProps): React.JSX.Element {
  const t = useT();
  const ugcGuard = useUgcColorGuard();
  const { message } = props;

  // §6.15:system 消息承载的是给模型的「不可信上下文」围栏(UNTRUSTED CONTEXT),
  // 绝不渲染其原始 content;仅以一条弱化提示告知用户上下文已纳入,杜绝原文外露。
  if (message.role === 'system') {
    return (
      <div
        className="mesh-chat__bubble mesh-chat__bubble--system"
        data-testid={`chat-message-${message.id}`}
        data-role="system"
      >
        <span className="mesh-chat__context-linked-chip" data-testid="chat-context-linked">
          {t('chat.message.contextLinked')}
        </span>
      </div>
    );
  }

  const isUser = message.role === 'user';
  const isAgent = message.role === 'agent';
  const isStreaming = message.generation_status === 'streaming';
  const isFailed = message.generation_status === 'failed';
  const isInterrupted = message.generation_status === 'interrupted';
  const bodyHtml = renderMarkdownPreview(message.content);

  const bubbleClass = [
    'mesh-chat__bubble',
    isUser ? 'mesh-chat__bubble--user' : 'mesh-chat__bubble--agent',
    isFailed ? 'mesh-chat__bubble--failed' : null,
  ]
    .filter((part): part is string => part !== null)
    .join(' ');

  return (
    <div
      className={bubbleClass}
      data-testid={`chat-message-${message.id}`}
      data-role={message.role}
    >
      <header className="mesh-chat__bubble-head">
        <span className="mesh-chat__bubble-role">
          {isUser ? t('chat.role.user') : t('chat.role.agent')}
        </span>
        {isAgent ? (
          <span className="mesh-chat__ai-badge" data-testid="chat-ai-badge">
            {t('chat.message.aiBadge')}
          </span>
        ) : null}
        {/* §9.8 运行反馈五态:agent 失败气泡头部统一徽标(interrupted 维持弱化提示,不徽标)。 */}
        {isAgent && isFailed ? (
          <RunStateBadge state="failed" label={t('runState.failed')} size="sm" />
        ) : null}
        <time className="mesh-chat__bubble-time" dateTime={message.created_at}>
          {formatRelativeTime(message.created_at, { locale: props.locale })}
        </time>
      </header>

      {props.quotedMessage !== null && props.quotedMessage !== undefined ? (
        <blockquote className="mesh-chat__quote" data-testid={`chat-quote-${message.id}`}>
          <span className="mesh-chat__quote-label">{t('chat.message.quotePrefix')}</span>
          <span className="mesh-chat__quote-text">
            {truncatePreview(props.quotedMessage.content)}
          </span>
        </blockquote>
      ) : null}

      {isFailed ? (
        <p
          className="mesh-chat__bubble-error"
          role="alert"
          data-testid={`chat-error-${message.id}`}
        >
          {message.error_message !== null && message.error_message !== ''
            ? message.error_message
            : t('chat.message.failed')}
        </p>
      ) : null}

      {isInterrupted ? (
        <p className="mesh-chat__bubble-interrupted" data-testid={`chat-interrupted-${message.id}`}>
          {t('chat.message.interrupted')}
        </p>
      ) : null}

      <div
        className="mesh-chat__bubble-body"
        data-testid={`chat-body-${message.id}`}
        ref={ugcGuard}
        // 聊天 content 为原始 Markdown:marked 解析后经 DOMPurify 白名单净化(markdown.ts)。
        // UGC 内联色对比兜底(theme.md §4.3 T5③)经 ugcGuard 回调 ref 执行。
        dangerouslySetInnerHTML={{ __html: bodyHtml }}
      />
      {isStreaming ? (
        <span
          className="mesh-chat__cursor"
          aria-label={t('chat.message.streaming')}
          data-testid={`chat-cursor-${message.id}`}
        />
      ) : null}

      {message.attachments.length > 0 ? (
        <MessageAttachments
          attachments={message.attachments}
          messageId={message.id}
          client={props.client}
        />
      ) : null}

      <footer className="mesh-chat__bubble-actions">
        {props.onQuote !== undefined ? (
          <button
            type="button"
            className="mesh-chat__action"
            data-testid={`chat-quote-action-${message.id}`}
            onClick={() => props.onQuote?.(message)}
          >
            {t('chat.action.quote')}
          </button>
        ) : null}
        {isAgent && props.onRegenerate !== undefined && !isStreaming ? (
          <Button
            variant="ghost"
            size="sm"
            data-testid={`chat-regenerate-${message.id}`}
            onClick={() => props.onRegenerate?.(message)}
          >
            {isFailed || isInterrupted ? t('chat.action.retry') : t('chat.action.regenerate')}
          </Button>
        ) : null}
      </footer>
    </div>
  );
}
