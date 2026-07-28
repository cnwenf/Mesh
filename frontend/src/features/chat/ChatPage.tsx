/**
 * 聊天页(chat-session.md §4.1):左会话列表 + 右会话面板的响应式布局。
 * 顶层编排:工作区解析(fetchMe/activeWorkspace)、会话列表拉取(过滤)、
 * workspace:{ws}:chat_sessions 频道列表级预览合并(applySessionListFrame)、
 * 置顶经 favorites 乐观切换(§6.19)、新建会话、选中会话渲染 ConversationPanel。
 * 流式主路径在 ConversationPanel/useChatStream(SSE);本页只编排列表与选择。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useIntl } from 'react-intl';
import { MeshApiClient, getToken } from '../../api';
import { EmptyState, ErrorState, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { listAgents } from '../agents/api';
import type { AgentSummary } from '../agents/types';
import { activeWorkspace, fetchMe } from '../members/api';
import {
  createChatSession,
  deleteSessionFavorite,
  listChatSessions,
  listSessionFavorites,
  putSessionFavorite,
  chatListChannel,
} from './api';
import { ConversationPanel } from './ConversationPanel';
import { toErrorKey } from './errors';
import { NewSessionDialog } from './NewSessionDialog';
import { applySessionListFrame } from './realtime';
import { SessionListPanel } from './SessionListPanel';
import type { SessionStatusFilter } from './SessionListPanel';
import type { ChatSession } from './types';
import './chat.css';

const SESSION_PAGE_LIMIT = 50;

export function ChatPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const intl = useIntl();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  const [agents, setAgents] = useState<readonly AgentSummary[]>([]);
  const [sessions, setSessions] = useState<readonly ChatSession[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [agentFilter, setAgentFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<SessionStatusFilter>('active');
  const [selected, setSelected] = useState<ChatSession | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  // 工作区解析(单一归属口径,同其他页面)。
  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        const active = activeWorkspace(me.memberships);
        if (cancelled) return;
        if (active === null) {
          setBootError('state.errorDescription');
          return;
        }
        setWorkspaceId(active.workspace_id);
      })
      .catch((err) => {
        if (!cancelled) setBootError(toErrorKey(err, 'state.errorDescription'));
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  // agent 名册(过滤下拉用;active)。
  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    listAgents(client, workspaceId, { status: 'active', limit: 50 })
      .then((page) => {
        if (!cancelled) setAgents(page.data);
      })
      .catch(() => {
        if (!cancelled) setAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId]);

  // 会话列表(过滤 + 分页首屏)。
  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    listChatSessions(client, workspaceId, {
      agent_id: agentFilter === '' ? undefined : agentFilter,
      status: statusFilter,
      limit: SESSION_PAGE_LIMIT,
    })
      .then(async (page) => {
        if (cancelled) return;
        // pinned 真源校正:以 favorites 列表覆写请求方快照(§6.19)。
        let favoriteIds: ReadonlySet<string> | null = null;
        try {
          const favorites = await listSessionFavorites(client, workspaceId);
          favoriteIds = new Set(favorites.map((entry) => entry.target_id));
        } catch {
          favoriteIds = null; // 收藏拉取失败不阻断列表;沿用服务端 pinned 快照。
        }
        if (cancelled) return;
        const normalized =
          favoriteIds === null
            ? page.data
            : page.data.map((session) => ({ ...session, pinned: favoriteIds.has(session.id) }));
        setSessions(normalized);
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
  }, [client, workspaceId, agentFilter, statusFilter, reloadKey]);

  // H1:列表级帧走 owner 私有频道 chat_list:{myMemberId}(全部会话均由本人持有,
  // owner_id 即本人 member id)。无会话时不订阅(也无需更新)。
  const myMemberId = sessions.length > 0 ? sessions[0].owner_id : null;
  useEffect(() => {
    if (realtime === null || myMemberId === null) return;
    const channel = chatListChannel(myMemberId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      setSessions((prev) => applySessionListFrame(prev, frame));
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, myMemberId]);

  const handleSelect = useCallback((session: ChatSession) => {
    setSelected(session);
  }, []);

  const handleSessionUpdated = useCallback((updated: ChatSession) => {
    setSelected(updated);
    setSessions((prev) => prev.map((session) => (session.id === updated.id ? updated : session)));
  }, []);

  const handleTogglePin = useCallback(
    async (session: ChatSession) => {
      const nextPinned = !session.pinned;
      const optimistic = { ...session, pinned: nextPinned };
      setSessions((prev) => prev.map((item) => (item.id === session.id ? optimistic : item)));
      setSelected((prev) => (prev !== null && prev.id === session.id ? optimistic : prev));
      try {
        if (nextPinned) await putSessionFavorite(client, session.id);
        else await deleteSessionFavorite(client, session.id);
      } catch (err) {
        // 回滚乐观置顶。
        setSessions((prev) => prev.map((item) => (item.id === session.id ? session : item)));
        setSelected((prev) => (prev !== null && prev.id === session.id ? session : prev));
        toast.addToast(t(toErrorKey(err)), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [client, toast, t],
  );

  const handleCreate = useCallback(
    async (agentId: string, contextIssueId: string | null, contextProjectId: string | null) => {
      if (workspaceId === null) return;
      const created = await createChatSession(client, workspaceId, {
        agent_id: agentId,
        context_issue_id: contextIssueId,
        context_project_id: contextProjectId,
      });
      setNewOpen(false);
      setSelected(created);
      setReloadKey((key) => key + 1);
    },
    [client, workspaceId],
  );

  if (bootError !== null) {
    return (
      <main className="mesh-chat" data-testid="chat-page">
        <ErrorState title={t('state.errorTitle')} description={t(bootError)} />
      </main>
    );
  }

  if (workspaceId === null) {
    return (
      <main className="mesh-chat" data-testid="chat-page">
        <Skeleton loadingLabel={t('common.loading')} />
      </main>
    );
  }

  return (
    <main className="mesh-chat" data-testid="chat-page">
      <SessionListPanel
        sessions={sessions}
        selectedId={selected !== null ? selected.id : null}
        locale={intl.locale}
        agents={agents}
        agentFilter={agentFilter}
        statusFilter={statusFilter}
        isLoading={isLoading}
        error={error}
        onAgentFilterChange={setAgentFilter}
        onStatusFilterChange={setStatusFilter}
        onSelect={handleSelect}
        onTogglePin={(session) => void handleTogglePin(session)}
        onNewSession={() => setNewOpen(true)}
        onRetry={() => setReloadKey((key) => key + 1)}
      />

      {selected !== null ? (
        <ConversationPanel
          client={client}
          workspaceId={workspaceId}
          session={selected}
          locale={intl.locale}
          onSessionUpdated={handleSessionUpdated}
        />
      ) : (
        <div className="mesh-chat__placeholder">
          <EmptyState
            title={t('chat.empty.selectTitle')}
            description={t('chat.empty.selectDescription')}
          />
        </div>
      )}

      <NewSessionDialog
        open={newOpen}
        client={client}
        workspaceId={workspaceId}
        onClose={() => setNewOpen(false)}
        onCreate={handleCreate}
      />
    </main>
  );
}
