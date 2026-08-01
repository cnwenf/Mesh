/**
 * 会话面板(chat-session.md §4.2):上下文条 + 消息列表(自动滚尾)+ 输入框 + 沉淀入口。
 * 流式主路径为 SSE(useChatStream 驱动 liveMessage);WS chat_session:{id} 频道仅在
 * 非流式态收到终态 message.* 时重拉对账(多端同步)。发送/重生成乐观更新,失败回滚;
 * 终态后并入 liveMessage 并重拉一次以校准候选/附件。中断经独立幂等 stop 端点。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import type { MeshApiClient } from '../../api';
import { getToken } from '../../api';
import { uuidv4 } from '../../api/uuid';
import { Button, ErrorState, Icon, IconButton, RunStateBadge, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import {
  chatSessionChannel,
  listChatMessages,
  regenerateMessage,
  sendMessage,
  stopGeneration,
} from './api';
import { ChatComposer } from './ChatComposer';
import type { ComposerSubmitOptions } from './ChatComposer';
import { ContextBar } from './ContextBar';
import { DistillDialog } from './DistillDialog';
import { buildDistillBody } from './distill';
import { toErrorKey } from './errors';
import { MessageList } from './MessageList';
import { useChatStream } from './useChatStream';
import type { ChatMessage, ChatSession } from './types';

const PAGE_LIMIT = 50;

/** mod+↑「编辑上一条」委派事件(ChatPage 登记快捷键,本面板持有消息真源)。 */
export const CHAT_EDIT_LAST_EVENT = 'mesh-chat-edit-last';
/** 会触发列表重拉的终态/创建事件(§3.6)。 */
const RELOAD_EVENTS: ReadonlySet<string> = new Set([
  'message.created',
  'message.done',
  'message.interrupted',
]);

function localId(): string {
  // uuidv4 而非裸 crypto.randomUUID():后者在 HTTP 非安全上下文缺失(MES-129)。
  return 'local-' + uuidv4();
}

function upsertById(list: readonly ChatMessage[], message: ChatMessage): ChatMessage[] {
  const exists = list.some((item) => item.id === message.id);
  return exists
    ? list.map((item) => (item.id === message.id ? message : item))
    : [...list, message];
}

export interface ConversationPanelProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly session: ChatSession;
  readonly locale: string;
  /** 会话字段变更(如移除上下文)后回写父级列表。 */
  readonly onSessionUpdated: (session: ChatSession) => void;
}

export function ConversationPanel(props: ConversationPanelProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const { client, workspaceId, session } = props;

  const [messages, setMessages] = useState<readonly ChatMessage[]>([]);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  // mod+↑ 编辑上一条自己消息:以草稿种子预填 composer 并聚焦(§4.3 S12)。
  const [draftSeed, setDraftSeed] = useState<{ readonly nonce: number; readonly content: string } | null>(null);
  useEffect(() => {
    const handleEditLast = (): void => {
      const lastOwn = [...messagesRef.current].reverse().find((message) => message.role === 'user');
      if (lastOwn !== undefined) {
        setDraftSeed({ nonce: Date.now(), content: lastOwn.content });
      }
    };
    window.addEventListener(CHAT_EDIT_LAST_EVENT, handleEditLast);
    return () => window.removeEventListener(CHAT_EDIT_LAST_EVENT, handleEditLast);
  }, []);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [quoteMessage, setQuoteMessage] = useState<ChatMessage | null>(null);
  const [distillOpen, setDistillOpen] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  const stream = useChatStream({ getToken });

  // 首屏拉取(时间倒序 → 反转为升序展示)。
  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    listChatMessages(client, workspaceId, session.id, { limit: PAGE_LIMIT })
      .then((page) => {
        if (cancelled) return;
        setMessages([...page.data].reverse());
        setNextCursor(page.nextCursor);
      })
      .catch((err) => {
        if (!cancelled) setError(toErrorKey(err, 'state.errorDescription'));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, session.id, reloadKey]);

  // 加载更旧消息(游标分页,倒序页反转后前置)。
  const loadOlder = useCallback(async () => {
    if (nextCursor === null || isLoadingMore) return;
    setIsLoadingMore(true);
    try {
      const page = await listChatMessages(client, workspaceId, session.id, {
        limit: PAGE_LIMIT,
        cursor: nextCursor,
      });
      setMessages((prev) => [...[...page.data].reverse(), ...prev]);
      setNextCursor(page.nextCursor);
    } catch (err) {
      toast.addToast(t(toErrorKey(err)), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setIsLoadingMore(false);
    }
  }, [nextCursor, isLoadingMore, client, workspaceId, session.id, toast, t]);

  // WS 对账:非流式态收到终态/创建帧 → 重拉(多端同步;SSE 为本地主路径)。
  useEffect(() => {
    if (realtime === null) return;
    const channel = chatSessionChannel(session.id);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (!RELOAD_EVENTS.has(frame.event)) return;
      if (stream.isStreaming) return;
      setReloadKey((key) => key + 1);
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, session.id, stream.isStreaming]);

  // 终态收口:并入 liveMessage,清空流,重拉一次校准候选/附件。
  useEffect(() => {
    if (stream.isStreaming) return;
    if (stream.liveMessage === null) return;
    const finalMessage = stream.liveMessage;
    setMessages((prev) => upsertById(prev, finalMessage));
    stream.reset();
    setReloadKey((key) => key + 1);
  }, [stream.isStreaming, stream.liveMessage, stream]);

  // 流内 error 事件 → toast 具体错误码。
  useEffect(() => {
    if (stream.streamError === null) return;
    toast.addToast(t(stream.streamError), { tone: 'danger', closeLabel: t('common.close') });
  }, [stream.streamError, toast, t]);

  // 自动滚尾:消息或流式内容变化时滚到底。
  const displayMessages = useMemo(() => {
    const live = stream.liveMessage;
    if (live === null) return messages;
    return messages.some((message) => message.id === live.id) ? messages : [...messages, live];
  }, [messages, stream.liveMessage]);

  useEffect(() => {
    const node = listRef.current;
    if (node !== null) node.scrollTop = node.scrollHeight;
  }, [displayMessages.length, stream.liveMessage?.content]);

  const handleSend = useCallback(
    async (content: string, opts: ComposerSubmitOptions): Promise<void> => {
      const optimistic: ChatMessage = {
        id: localId(),
        session_id: session.id,
        role: 'user',
        content,
        generation_id: null,
        generation_status: 'done',
        parent_id: null,
        selected_candidate: true,
        quote_message_id: opts.quoteMessageId,
        prompt_tokens: null,
        completion_tokens: null,
        error_message: null,
        started_at: null,
        finished_at: null,
        created_at: new Date().toISOString(),
        attachments: opts.attachmentRefs,
        candidate_count: null,
        candidate_index: null,
      };
      setMessages((prev) => [...prev, optimistic]);
      setQuoteMessage(null);
      try {
        const result = await sendMessage(client, workspaceId, session.id, {
          content,
          attachment_ids: opts.attachmentIds,
          quote_message_id: opts.quoteMessageId,
        });
        setMessages((prev) =>
          prev.map((message) =>
            message.id === optimistic.id ? { ...message, id: result.message_id } : message,
          ),
        );
        stream.start({
          streamUrl: result.stream_url,
          generationId: result.generation_id,
          sessionId: session.id,
        });
      } catch (err) {
        setMessages((prev) => prev.filter((message) => message.id !== optimistic.id));
        toast.addToast(t(toErrorKey(err)), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
        throw err;
      }
    },
    [client, workspaceId, session.id, stream, toast, t],
  );

  const handleStop = useCallback(async () => {
    const generationId = stream.activeGenerationId;
    if (generationId === null) return;
    stream.abort();
    try {
      await stopGeneration(client, workspaceId, session.id, generationId);
    } catch {
      // stop 幂等;失败不阻断本地中断体验。
    }
  }, [stream, client, workspaceId, session.id]);

  const handleRegenerate = useCallback(
    async (message: ChatMessage) => {
      try {
        const result = await regenerateMessage(client, workspaceId, session.id, message.id);
        stream.start({
          streamUrl: result.stream_url,
          generationId: result.generation_id,
          sessionId: session.id,
        });
      } catch (err) {
        toast.addToast(t(toErrorKey(err)), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [client, workspaceId, session.id, stream, toast, t],
  );

  const handleSelectCandidate = useCallback((selected: ChatMessage) => {
    setMessages((prev) => upsertById(prev, selected));
  }, []);

  const distillAttachmentIds = useMemo(
    () => messages.flatMap((message) => message.attachments.map((attachment) => attachment.id)),
    [messages],
  );
  const canDistill = session.context_issue_id !== null;
  const isArchived = session.status !== 'active';
  // §9.8 运行反馈五态(统一语言):尚无流内容 → queued(已排队),有内容 → running(运行中)。
  const liveRunState =
    stream.liveMessage !== null && stream.liveMessage.content !== '' ? 'running' : 'queued';

  return (
    <section className="mesh-chat__conversation" data-testid="chat-conversation">
      <header className="mesh-chat__conversation-head">
        {/* 手机单栏(detail 窗格)返回列表入口;桌面经 CSS 隐藏(§8.3 明确返回)。 */}
        <IconButton
          label={t('chat.back')}
          variant="ghost"
          className="mesh-chat__back"
          data-testid="chat-back"
          onClick={() => navigate('/chat')}
        >
          <Icon name="chevron-left" />
        </IconButton>
        <h2 className="mesh-chat__conversation-title" data-testid="chat-conversation-title">
          {session.title}
        </h2>
        {stream.isStreaming ? (
          <>
            <span className="mesh-chat__run-state" data-testid="chat-run-state">
              <RunStateBadge state={liveRunState} label={t(`runState.${liveRunState}`)} size="sm" />
            </span>
            <Button
              variant="danger"
              size="sm"
              data-testid="chat-stop"
              onClick={() => void handleStop()}
            >
              {t('chat.action.stop')}
            </Button>
          </>
        ) : null}
        <Button
          variant="secondary"
          size="sm"
          data-testid="chat-distill-open"
          disabled={!canDistill}
          onClick={() => setDistillOpen(true)}
        >
          {t('chat.action.distill')}
        </Button>
      </header>

      {/* 上下文条:issue/project 关联展示·移除·添加更换(§4.2),内聚于 ContextBar。 */}
      <ContextBar
        client={client}
        workspaceId={workspaceId}
        session={session}
        onSessionUpdated={props.onSessionUpdated}
      />

      {error !== null ? (
        <ErrorState
          title={t('state.errorTitle')}
          description={t(error)}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : isLoading ? (
        <Skeleton loadingLabel={t('common.loading')} />
      ) : (
        <MessageList
          messages={messages}
          displayMessages={displayMessages}
          locale={props.locale}
          nextCursor={nextCursor}
          isLoadingMore={isLoadingMore}
          client={client}
          workspaceId={workspaceId}
          sessionId={session.id}
          listRef={listRef}
          onLoadOlder={() => void loadOlder()}
          onRegenerate={handleRegenerate}
          onSelectCandidate={handleSelectCandidate}
          onQuote={setQuoteMessage}
        />
      )}

      <ChatComposer
        onSend={handleSend}
        quoteMessage={quoteMessage}
        onClearQuote={() => setQuoteMessage(null)}
        disabled={isArchived || stream.isStreaming}
        draftSeed={draftSeed}
        workspaceId={workspaceId}
      />

      <DistillDialog
        open={distillOpen}
        client={client}
        workspaceId={workspaceId}
        sessionId={session.id}
        initialBody={buildDistillBody(messages, t('chat.role.user'), t('chat.role.agent'))}
        targetIssueId={session.context_issue_id ?? ''}
        attachmentIds={distillAttachmentIds}
        onClose={() => setDistillOpen(false)}
        onDistilled={(issueId) => {
          setDistillOpen(false);
          navigate(`/issues/${issueId}`);
        }}
      />
    </section>
  );
}
