/**
 * 聊天页(chat-session.md §4.1):左会话列表 + 右会话面板的响应式布局。
 * 顶层编排:工作区解析(WorkspaceProvider 权威 membership)、会话列表拉取(过滤)、
 * workspace:{ws}:chat_sessions 频道列表级预览合并(applySessionListFrame)、
 * 置顶经 favorites 乐观切换(§6.19)、新建会话、选中会话渲染 ConversationPanel。
 * 流式主路径在 ConversationPanel/useChatStream(SSE);本页只编排列表与选择。
 *
 * 选中态路由化(design-quality §4.4 Conversation 模板):选中会话由 URL 参数
 * `:sessionId` 驱动(/chat 与 /chat/:sessionId 同渲染本页),桌面双栏同步、
 * 手机经 ConversationLayout activePane 列表/会话单栏切换;深链(参数不在已加载
 * 列表)经 getChatSession 一次性引导,404/错误回退占位不崩。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useIntl } from 'react-intl';
import { useNavigate, useParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import { ConversationLayout, EmptyState, ErrorState, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { usePageContext, useShortcutRegistry } from '../../shortcuts';
import { useOptionalWorkspace } from '../../workspace/WorkspaceProvider';
import { listAgents } from '../agents/api';
import type { AgentSummary } from '../agents/types';
import { listMembers } from '../members/api';
import type { HumanProfile, MemberSummary } from '../members/types';
import { useWorkspaceMembership, workspaceRoute } from '../members/useWorkspaceMembership';
import {
  createChatSession,
  deleteSessionFavorite,
  getChatSession,
  listChatSessions,
  listSessionFavorites,
  putSessionFavorite,
  chatListChannel,
} from './api';
import { CHAT_EDIT_LAST_EVENT, ConversationPanel } from './ConversationPanel';
import { toErrorKey } from './errors';
import { NewSessionDialog } from './NewSessionDialog';
import { applySessionListFrame } from './realtime';
import { SessionListPanel } from './SessionListPanel';
import type { SessionStatusFilter } from './SessionListPanel';
import type { ChatSession } from './types';
import './chat.css';

const SESSION_PAGE_LIMIT = 50;

function matchCurrentMemberId(
  members: readonly MemberSummary[],
  user: { readonly id: string; readonly email: string },
): string | null {
  for (const member of members) {
    if (member.member_type !== 'human' || member.profile === null) continue;
    const profile = member.profile as HumanProfile;
    if (profile.id === user.id || profile.email === user.email) return member.id;
  }
  return null;
}

export function ChatPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const intl = useIntl();
  const navigate = useNavigate();
  const { sessionId } = useParams();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const provider = useOptionalWorkspace();
  const membershipState = useWorkspaceMembership(client);
  const workspaceId =
    membershipState.kind === 'ready' ? membershipState.membership.workspace_id : null;
  const workspaceSlug =
    provider !== null && provider.status === 'ready' && provider.workspace !== null
      ? provider.workspace.slug
      : null;
  const chatPath = workspaceSlug === null ? '/chat' : workspaceRoute(workspaceSlug, 'chat');

  const [agents, setAgents] = useState<readonly AgentSummary[]>([]);
  const [sessions, setSessions] = useState<readonly ChatSession[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [agentFilter, setAgentFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<SessionStatusFilter>('active');
  /** 深链引导/新建落地:尚未进入列表的选中会话快照(列表命中优先)。 */
  const [bootstrappedSession, setBootstrappedSession] = useState<ChatSession | null>(null);
  /** 深链引导去重:`${workspaceId}:${sessionId}` 仅拉一次(404 不循环重试)。 */
  const bootstrapAttemptRef = useRef<string | null>(null);
  /** H6:深链会话确证不存在/失效(引导 404 且列表无命中)。 */
  const [sessionNotFound, setSessionNotFound] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [resolvedMember, setResolvedMember] = useState<{
    readonly targetKey: string;
    readonly memberId: string | null;
  }>({ targetKey: '', memberId: null });

  // 工作区切换先清除旧租户快照;新列表/名册返回前绝不短暂展示或订阅旧数据。
  useEffect(() => {
    setAgents([]);
    setSessions([]);
    setBootstrappedSession(null);
    setResolvedMember({ targetKey: '', memberId: null });
    bootstrapAttemptRef.current = null;
  }, [workspaceId]);

  // 聊天上下文独占激活(§2.1:chat 与 board/issue 不叠加)。Enter 发送 / Shift+Enter
  // 换行 / Esc 失焦的实际语义在 ChatComposer(输入控件原生键,分发层第 1 层放行);
  // 此处登记命令供帮助层呈现与焦点动作;mod+↑ 经事件委派给持有消息的 ConversationPanel。
  usePageContext('chat');
  useEffect(() => {
    const registry = useShortcutRegistry.getState();
    const focusComposer = () => {
      document.querySelector<HTMLElement>('[data-testid="chat-composer-input"]')?.focus();
    };
    return registry.registerShortcuts([
      { id: 'chat.send', combo: 'enter', label: t('shortcuts.chatSend'), group: 'chat', run: focusComposer },
      { id: 'chat.newline', combo: 'shift+enter', label: t('shortcuts.chatNewline'), group: 'chat', run: focusComposer },
      {
        id: 'chat.edit.last',
        combo: 'mod+arrowup',
        label: t('shortcuts.chatEditLast'),
        group: 'chat',
        run: () => window.dispatchEvent(new CustomEvent(CHAT_EDIT_LAST_EVENT)),
      },
      {
        id: 'chat.blur',
        combo: 'esc',
        label: t('shortcuts.chatBlur'),
        group: 'chat',
        run: () => {
          if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
        },
      },
    ]);
  }, [t]);

  // 当前成员 id 独立于会话列表解析:即使列表为空/被筛空,仍订阅本人私有列表频道。
  useEffect(() => {
    if (membershipState.kind !== 'ready') return;
    // 兼容不完整的隔离测试桩;生产 `/users/me` 始终携带 user。
    if (membershipState.user === undefined) return;
    const targetKey = `${membershipState.membership.workspace_id}:${membershipState.user.id}`;
    let cancelled = false;
    setResolvedMember({ targetKey, memberId: null });
    listMembers(client, membershipState.membership.workspace_id, { limit: 100 })
      .then((page) => {
        if (!cancelled) {
          setResolvedMember({
            targetKey,
            memberId: matchCurrentMemberId(page.data, membershipState.user),
          });
        }
      })
      .catch(() => {
        if (!cancelled) setResolvedMember({ targetKey, memberId: null });
      });
    return () => {
      cancelled = true;
    };
  }, [client, membershipState]);

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

  // H1:列表级帧走 owner 私有频道 chat_list:{myMemberId};名册是真源,已有会话的
  // owner_id 仅在名册暂不可用时作兼容兜底,不会再令空列表失去订阅。
  const memberTargetKey =
    membershipState.kind === 'ready'
      ? `${membershipState.membership.workspace_id}:${membershipState.user?.id ?? 'unknown'}`
      : null;
  const myMemberId =
    memberTargetKey === null
      ? null
      : resolvedMember.targetKey === memberTargetKey
        ? (resolvedMember.memberId ?? sessions[0]?.owner_id ?? null)
        : (sessions[0]?.owner_id ?? null);
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

  // 路由离开会话(参数归无)→ 清空引导快照,选中派生回落到占位。
  useEffect(() => {
    if (sessionId === undefined) setBootstrappedSession(null);
    setSessionNotFound(false);
  }, [sessionId]);

  // 深链引导:参数会话不在已加载列表(且无引导快照)时一次性拉取;
  // 404/错误 → 快照置空 → 派生选中为 null(占位),不崩、不循环。
  useEffect(() => {
    if (workspaceId === null || sessionId === undefined) return;
    if (isLoading) return;
    if (sessions.some((session) => session.id === sessionId)) return;
    if (bootstrappedSession !== null && bootstrappedSession.id === sessionId) return;
    const attemptKey = `${workspaceId}:${sessionId}`;
    if (bootstrapAttemptRef.current === attemptKey) return;
    bootstrapAttemptRef.current = attemptKey;
    let cancelled = false;
    getChatSession(client, workspaceId, sessionId)
      .then((session) => {
        if (!cancelled) setBootstrappedSession(session);
      })
      .catch(() => {
        if (!cancelled) {
          setBootstrappedSession(null);
          setSessionNotFound(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, sessionId, sessions, isLoading, bootstrappedSession]);

  // 选中态由 URL 派生:列表命中优先,其后深链/新建引导快照(限同 id)。
  const selected = useMemo<ChatSession | null>(() => {
    if (sessionId === undefined) return null;
    const inList = sessions.find((session) => session.id === sessionId);
    if (inList !== undefined) return inList;
    return bootstrappedSession !== null && bootstrappedSession.id === sessionId
      ? bootstrappedSession
      : null;
  }, [sessionId, sessions, bootstrappedSession]);
  const pageTitle =
    selected?.title ??
    (sessionId !== undefined ? t('chat.conversation.label') : t('chat.list.label'));

  // H6:确证坏/失效深链 → 回列表路由,消除手机死胡同(§8.3)与桌面悬空 URL。
  useEffect(() => {
    if (sessionId !== undefined && sessionNotFound) {
      navigate(chatPath, { replace: true });
    }
  }, [sessionId, sessionNotFound, navigate, chatPath]);

  const handleSelect = useCallback(
    (session: ChatSession) => {
      navigate(`${chatPath}/${session.id}`);
    },
    [chatPath, navigate],
  );

  const handleSessionUpdated = useCallback((updated: ChatSession) => {
    setBootstrappedSession((prev) => (prev !== null && prev.id === updated.id ? updated : prev));
    setSessions((prev) => prev.map((session) => (session.id === updated.id ? updated : session)));
  }, []);

  const handleTogglePin = useCallback(
    async (session: ChatSession) => {
      const nextPinned = !session.pinned;
      const optimistic = { ...session, pinned: nextPinned };
      setSessions((prev) => prev.map((item) => (item.id === session.id ? optimistic : item)));
      try {
        if (nextPinned) await putSessionFavorite(client, session.id);
        else await deleteSessionFavorite(client, session.id);
      } catch (err) {
        // 回滚乐观置顶。
        setSessions((prev) => prev.map((item) => (item.id === session.id ? session : item)));
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
      // 先落引导快照再导航:列表重拉 settle 前即可渲染会话面板。
      setBootstrappedSession(created);
      navigate(`${chatPath}/${created.id}`);
      setReloadKey((key) => key + 1);
    },
    [chatPath, client, workspaceId, navigate],
  );

  if (membershipState.kind === 'error' || membershipState.kind === 'no_workspace') {
    return (
      <div className="mesh-chat-page" data-testid="chat-page">
        <h1 className="sr-only">{pageTitle}</h1>
        <ErrorState title={t('state.errorTitle')} description={t('state.errorDescription')} />
      </div>
    );
  }

  if (membershipState.kind === 'loading' || workspaceId === null) {
    return (
      <div className="mesh-chat-page" data-testid="chat-page">
        <h1 className="sr-only">{pageTitle}</h1>
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }

  return (
    <div className="mesh-chat-page" data-testid="chat-page">
      <h1 className="sr-only">{pageTitle}</h1>
      <ConversationLayout
        className="mesh-chat"
        listLabel={t('chat.list.label')}
        detailLabel={t('chat.conversation.label')}
        activePane={sessionId !== undefined ? 'detail' : 'list'}
        list={
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
        }
      >
        {selected !== null ? (
          <ConversationPanel
            key={selected.id}
            client={client}
            workspaceId={workspaceId}
            workspaceSlug={workspaceSlug}
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
      </ConversationLayout>

      <NewSessionDialog
        open={newOpen}
        client={client}
        workspaceId={workspaceId}
        onClose={() => setNewOpen(false)}
        onCreate={handleCreate}
      />
    </div>
  );
}
