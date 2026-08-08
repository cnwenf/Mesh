/**
 * 会话列表面板(chat-session.md §4.1):置顶区在上(session.pinned 真源 favorites,§6.19),
 * 中间为最近活跃会话,归档区固定在底部(spec §4.1「置顶在上…归档区在底部」);
 * agent 过滤 + 新建入口;行内预览 + 相对时间(formatRelativeTime);点击打开,
 * 行内 pin/unpin、归档/取消归档、删除(对话框二次确认)。
 * 纯展示 + 回调;数据获取/置顶/归档/删除落库在父级(ChatPage)。
 */
import { useState } from 'react';
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Icon,
  IconButton,
  Select,
  Skeleton,
} from '../../design';
import { formatRelativeTime, useT } from '../../i18n';
import type { AgentSummary } from '../agents/types';
import { EmptyChatBubbles } from '../onboarding/illustrations';
import { AgentAvatar } from './AgentAvatar';
import type { ChatSession } from './types';

/**
 * 会话搜索过滤(§4.1):对已加载会话做客户端大小写不敏感子串匹配,命中 title 或
 * agent 名称即保留;空查询不过滤。后端列表端点无搜索参数,故搜索纯前端。
 */
function matchesQuery(session: ChatSession, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === '') return true;
  return (
    session.title.toLowerCase().includes(needle) ||
    session.agent.name.toLowerCase().includes(needle)
  );
}

export interface SessionListPanelProps {
  readonly sessions: readonly ChatSession[];
  readonly selectedId: string | null;
  readonly locale: string;
  readonly agents: readonly AgentSummary[];
  readonly agentFilter: string;
  readonly isLoading: boolean;
  readonly error: string | null;
  readonly onAgentFilterChange: (agentId: string) => void;
  readonly onSelect: (session: ChatSession) => void;
  readonly onTogglePin: (session: ChatSession) => void;
  /** 归档/取消归档(父级经 PATCH status + If-Match 落库,§6.14)。 */
  readonly onToggleArchive: (session: ChatSession) => void;
  /** 删除(父级经 DELETE 软删;本面板负责对话框确认)。 */
  readonly onDelete: (session: ChatSession) => void;
  readonly onNewSession: () => void;
  readonly onRetry: () => void;
}

interface SessionRowProps {
  readonly session: ChatSession;
  readonly selected: boolean;
  readonly locale: string;
  readonly pinLabel: string;
  readonly unpinLabel: string;
  readonly archiveLabel: string;
  readonly unarchiveLabel: string;
  readonly deleteLabel: string;
  readonly onSelect: (session: ChatSession) => void;
  readonly onTogglePin: (session: ChatSession) => void;
  readonly onToggleArchive: (session: ChatSession) => void;
  readonly onRequestDelete: (session: ChatSession) => void;
}

function SessionRow(props: SessionRowProps): React.JSX.Element {
  const t = useT();
  const { session } = props;
  const isArchived = session.status === 'archived';
  return (
    <li
      className={
        props.selected ? 'mesh-chat__session mesh-chat__session--selected' : 'mesh-chat__session'
      }
      data-testid={`chat-session-${session.id}`}
      data-selected={props.selected}
    >
      <AgentAvatar agent={session.agent} testId={`chat-session-avatar-${session.id}`} />
      <button
        type="button"
        className="mesh-chat__session-main"
        onClick={() => props.onSelect(session)}
      >
        <span className="mesh-chat__session-headline">
          <span className="mesh-chat__session-title">{session.title}</span>
          {session.last_message_at !== null ? (
            <time className="mesh-chat__session-time">
              {formatRelativeTime(session.last_message_at, { locale: props.locale })}
            </time>
          ) : null}
        </span>
        <span className="mesh-chat__session-subline">
          <span className="mesh-chat__session-agent">{session.agent.name}</span>
          <span className="mesh-chat__session-separator" aria-hidden="true">
            ·
          </span>
          <span
            className="mesh-chat__session-preview"
            data-testid={`chat-session-preview-${session.id}`}
          >
            {session.last_message_preview ?? t('chat.session.noPreview')}
          </span>
        </span>
      </button>
      <span className="mesh-chat__session-actions">
        {isArchived ? null : (
          <IconButton
            label={session.pinned ? props.unpinLabel : props.pinLabel}
            className="mesh-chat__session-pin"
            data-testid={`chat-session-pin-${session.id}`}
            data-pinned={session.pinned}
            onClick={() => props.onTogglePin(session)}
          >
            {session.pinned ? (
              <Icon name="star" size={16} filled />
            ) : (
              <Icon name="star" size={16} />
            )}
          </IconButton>
        )}
        <IconButton
          label={isArchived ? props.unarchiveLabel : props.archiveLabel}
          className="mesh-chat__session-archive"
          data-testid={`chat-session-archive-${session.id}`}
          onClick={() => props.onToggleArchive(session)}
        >
          <Icon name="archive" size={16} />
        </IconButton>
        <IconButton
          label={props.deleteLabel}
          className="mesh-chat__session-delete"
          data-testid={`chat-session-delete-${session.id}`}
          onClick={() => props.onRequestDelete(session)}
        >
          <Icon name="trash" size={16} />
        </IconButton>
      </span>
    </li>
  );
}

export function SessionListPanel(props: SessionListPanelProps): React.JSX.Element {
  const t = useT();
  // 搜索为纯客户端状态(§4.1):过滤已加载会话,不回传父级、不触发请求。
  const [query, setQuery] = useState('');
  /** 删除二次确认目标;null = 对话框关闭。 */
  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null);
  const filtered = props.sessions.filter((session) => matchesQuery(session, query));
  // §4.1 布局:置顶在上 → 最近 → 归档区固定在底部。
  const pinned = filtered.filter((session) => session.pinned && session.status === 'active');
  const recent = filtered.filter((session) => !session.pinned && session.status === 'active');
  const archived = filtered.filter((session) => session.status === 'archived');
  const pinLabel = t('chat.session.pin');
  const unpinLabel = t('chat.session.unpin');
  const archiveLabel = t('chat.session.archive');
  const unarchiveLabel = t('chat.session.unarchive');
  const deleteLabel = t('chat.session.delete');

  const renderRow = (session: ChatSession): React.JSX.Element => (
    <SessionRow
      key={session.id}
      session={session}
      selected={session.id === props.selectedId}
      locale={props.locale}
      pinLabel={pinLabel}
      unpinLabel={unpinLabel}
      archiveLabel={archiveLabel}
      unarchiveLabel={unarchiveLabel}
      deleteLabel={deleteLabel}
      onSelect={props.onSelect}
      onTogglePin={props.onTogglePin}
      onToggleArchive={props.onToggleArchive}
      onRequestDelete={setDeleteTarget}
    />
  );

  return (
    <aside className="mesh-chat__sessions" data-testid="chat-session-panel">
      <header className="mesh-chat__sessions-head">
        <h2 className="mesh-chat__sessions-title mesh-text-body-strong">{t('nav.chat')}</h2>
        <Button
          size="sm"
          variant="ghost"
          data-testid="chat-new-session"
          onClick={props.onNewSession}
        >
          {t('chat.session.new')}
        </Button>
      </header>
      <div className="mesh-chat__sessions-search">
        <input
          type="search"
          className="mesh-chat__search-input"
          data-testid="chat-session-search"
          value={query}
          placeholder={t('chat.session.searchPlaceholder')}
          aria-label={t('chat.session.searchPlaceholder')}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="mesh-chat__sessions-toolbar">
        <Select
          label={t('chat.filter.agentLabel')}
          value={props.agentFilter}
          data-testid="chat-filter-agent"
          onChange={(event) => props.onAgentFilterChange(event.target.value)}
        >
          <option value="">{t('chat.filter.agentAll')}</option>
          {props.agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.display_name !== '' ? agent.display_name : agent.name}
            </option>
          ))}
        </Select>
      </div>

      {props.error !== null ? (
        <ErrorState
          title={t('state.errorTitle')}
          description={t(props.error)}
          retryLabel={t('common.retry')}
          onRetry={props.onRetry}
        />
      ) : props.isLoading ? (
        <Skeleton loadingLabel={t('common.loading')} />
      ) : props.sessions.length === 0 ? (
        <EmptyState
          illustration={<EmptyChatBubbles />}
          title={t('onboarding.empty.chat.title')}
          description={t('onboarding.empty.chat.description')}
          action={
            <Button size="sm" data-testid="chat-empty-new" onClick={props.onNewSession}>
              {t('onboarding.empty.chat.action')}
            </Button>
          }
        />
      ) : filtered.length === 0 ? (
        // 有会话但搜索无命中(§4.1):区别于「无会话」空态。
        <div data-testid="chat-search-empty">
          <EmptyState title={t('state.emptyTitle')} description={t('state.emptyDescription')} />
        </div>
      ) : (
        <div className="mesh-chat__sessions-scroll">
          {pinned.length > 0 ? (
            <>
              <h3 className="mesh-chat__sessions-group">{t('chat.session.pinnedGroup')}</h3>
              <ul className="mesh-chat__session-list">{pinned.map(renderRow)}</ul>
            </>
          ) : null}
          {recent.length > 0 ? (
            <>
              <h3 className="mesh-chat__sessions-group">{t('chat.session.recentGroup')}</h3>
              <ul className="mesh-chat__session-list">{recent.map(renderRow)}</ul>
            </>
          ) : null}
          {archived.length > 0 ? (
            <div className="mesh-chat__sessions-archived" data-testid="chat-sessions-archived">
              <h3 className="mesh-chat__sessions-group">{t('chat.session.archivedGroup')}</h3>
              <ul className="mesh-chat__session-list">{archived.map(renderRow)}</ul>
            </div>
          ) : null}
        </div>
      )}

      <Dialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title={t('chat.session.deleteDialogTitle')}
        closeLabel={t('common.close')}
      >
        <p data-testid="chat-session-delete-confirm-text">{t('chat.session.deleteConfirmText')}</p>
        <div className="mesh-chat__delete-confirm">
          <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            data-testid="chat-session-delete-confirm"
            onClick={() => {
              if (deleteTarget !== null) props.onDelete(deleteTarget);
              setDeleteTarget(null);
            }}
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </aside>
  );
}
