/**
 * 会话列表面板(chat-session.md §4.1)。置顶区在上(session.pinned 真源 favorites,§6.19),
 * 其余按 last_message_at 倒序(后端已排);agent 过滤 + 状态过滤 + 新建入口;
 * 行内预览 + 相对时间(formatRelativeTime);点击打开,行内 pin/unpin 切换。
 * 纯展示 + 回调;数据获取/置顶落库在父级(ChatPage)。
 */
import { useState } from 'react';
import { Button, EmptyState, ErrorState, Icon, IconButton, Select, Skeleton } from '../../design';
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

export type SessionStatusFilter = 'active' | 'archived';

export interface SessionListPanelProps {
  readonly sessions: readonly ChatSession[];
  readonly selectedId: string | null;
  readonly locale: string;
  readonly agents: readonly AgentSummary[];
  readonly agentFilter: string;
  readonly statusFilter: SessionStatusFilter;
  readonly isLoading: boolean;
  readonly error: string | null;
  readonly onAgentFilterChange: (agentId: string) => void;
  readonly onStatusFilterChange: (status: SessionStatusFilter) => void;
  readonly onSelect: (session: ChatSession) => void;
  readonly onTogglePin: (session: ChatSession) => void;
  readonly onNewSession: () => void;
  readonly onRetry: () => void;
}

interface SessionRowProps {
  readonly session: ChatSession;
  readonly selected: boolean;
  readonly locale: string;
  readonly pinLabel: string;
  readonly unpinLabel: string;
  readonly onSelect: (session: ChatSession) => void;
  readonly onTogglePin: (session: ChatSession) => void;
}

function SessionRow(props: SessionRowProps): React.JSX.Element {
  const t = useT();
  const { session } = props;
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
        <span className="mesh-chat__session-title">{session.title}</span>
        <span className="mesh-chat__session-agent">{session.agent.name}</span>
        <span
          className="mesh-chat__session-preview"
          data-testid={`chat-session-preview-${session.id}`}
        >
          {session.last_message_preview ?? t('chat.session.noPreview')}
        </span>
        {session.last_message_at !== null ? (
          <time className="mesh-chat__session-time">
            {formatRelativeTime(session.last_message_at, { locale: props.locale })}
          </time>
        ) : null}
      </button>
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
    </li>
  );
}

export function SessionListPanel(props: SessionListPanelProps): React.JSX.Element {
  const t = useT();
  // 搜索为纯客户端状态(§4.1):过滤已加载会话,不回传父级、不触发请求。
  const [query, setQuery] = useState('');
  const filtered = props.sessions.filter((session) => matchesQuery(session, query));
  const pinned = filtered.filter((session) => session.pinned);
  const others = filtered.filter((session) => !session.pinned);
  const pinLabel = t('chat.session.pin');
  const unpinLabel = t('chat.session.unpin');

  const renderRow = (session: ChatSession): React.JSX.Element => (
    <SessionRow
      key={session.id}
      session={session}
      selected={session.id === props.selectedId}
      locale={props.locale}
      pinLabel={pinLabel}
      unpinLabel={unpinLabel}
      onSelect={props.onSelect}
      onTogglePin={props.onTogglePin}
    />
  );

  return (
    <aside className="mesh-chat__sessions" data-testid="chat-session-panel">
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
        <Button size="sm" data-testid="chat-new-session" onClick={props.onNewSession}>
          {t('chat.session.new')}
        </Button>
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
        <Select
          label={t('chat.filter.statusLabel')}
          value={props.statusFilter}
          data-testid="chat-filter-status"
          onChange={(event) =>
            props.onStatusFilterChange(event.target.value as SessionStatusFilter)
          }
        >
          <option value="active">{t('chat.filter.statusActive')}</option>
          <option value="archived">{t('chat.filter.statusArchived')}</option>
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
          {others.length > 0 ? (
            <>
              <h3 className="mesh-chat__sessions-group">{t('chat.session.recentGroup')}</h3>
              <ul className="mesh-chat__session-list">{others.map(renderRow)}</ul>
            </>
          ) : null}
        </div>
      )}
    </aside>
  );
}
