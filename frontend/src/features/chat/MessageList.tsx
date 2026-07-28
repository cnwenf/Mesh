/**
 * 消息列表渲染(chat-session.md §4.2):升序气泡列 + 加载更旧 + 空态 + 自动滚尾容器;
 * agent 多候选消息附带 CandidateSwitcher;引用经 quote_message_id 在已提交消息中解析。
 * 纯展示 + 回调;数据获取/流式/分页状态在父级(ConversationPanel)。
 */
import type { RefObject } from 'react';
import type { MeshApiClient } from '../../api';
import { Button, EmptyState } from '../../design';
import { useT } from '../../i18n';
import { CandidateSwitcher } from './CandidateSwitcher';
import { MessageBubble } from './MessageBubble';
import type { ChatMessage } from './types';

export interface MessageListProps {
  /** 已提交消息(引用解析用) */
  readonly messages: readonly ChatMessage[];
  /** 展示消息(已提交 + 进行中的实时消息) */
  readonly displayMessages: readonly ChatMessage[];
  readonly locale: string;
  readonly nextCursor: string | null;
  readonly isLoadingMore: boolean;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly sessionId: string;
  readonly listRef: RefObject<HTMLDivElement | null>;
  readonly onLoadOlder: () => void;
  readonly onRegenerate: (message: ChatMessage) => void;
  readonly onSelectCandidate: (message: ChatMessage) => void;
  readonly onQuote: (message: ChatMessage) => void;
}

export function MessageList(props: MessageListProps): React.JSX.Element {
  const t = useT();

  // 引用解析:在已提交消息中按 quote_message_id 找原消息(候选预览逐条解析)。
  const resolveQuoted = (message: ChatMessage): ChatMessage | null =>
    message.quote_message_id !== null
      ? (props.messages.find((item) => item.id === message.quote_message_id) ?? null)
      : null;

  const renderBubble = (message: ChatMessage): React.JSX.Element => (
    <MessageBubble
      message={message}
      locale={props.locale}
      client={props.client}
      quotedMessage={resolveQuoted(message)}
      onRegenerate={props.onRegenerate}
      onQuote={props.onQuote}
    />
  );

  const renderMessage = (message: ChatMessage): React.JSX.Element => {
    const showCandidates =
      message.role === 'agent' &&
      message.parent_id !== null &&
      message.candidate_count !== null &&
      message.candidate_count > 1;
    // 多候选:切换器自持「本地查看索引」,经 renderCandidate 渲染当前查看候选的气泡;
    // ‹ › 翻页不写库,「使用此条」才落库(§4.2,见 CandidateSwitcher)。
    if (showCandidates) {
      return (
        <div key={message.id} className="mesh-chat__message-row">
          <CandidateSwitcher
            client={props.client}
            workspaceId={props.workspaceId}
            sessionId={props.sessionId}
            parentId={message.parent_id as string}
            selectedId={message.id}
            index={message.candidate_index ?? 0}
            count={message.candidate_count as number}
            selectedMessage={message}
            renderCandidate={renderBubble}
            onSelected={props.onSelectCandidate}
          />
        </div>
      );
    }
    return (
      <div key={message.id} className="mesh-chat__message-row">
        {renderBubble(message)}
      </div>
    );
  };

  return (
    <div className="mesh-chat__messages" ref={props.listRef} data-testid="chat-messages">
      {props.nextCursor !== null ? (
        <Button
          variant="ghost"
          size="sm"
          data-testid="chat-load-older"
          isLoading={props.isLoadingMore}
          onClick={props.onLoadOlder}
        >
          {t('chat.action.loadOlder')}
        </Button>
      ) : null}
      {props.displayMessages.length === 0 ? (
        <EmptyState title={t('chat.empty.title')} description={t('chat.empty.description')} />
      ) : (
        props.displayMessages.map(renderMessage)
      )}
    </div>
  );
}
