/**
 * 看板页面(kanban.md §4.1/§4.2/§4.3/§4.4,README §6.12 异常态矩阵)。
 *
 * 投影层(MES-33):在视图定义层 shell 上接真实 issue 数据 ——
 * - GET /views/{id}/issues 执行视图配置 → 分组整体游标 → 渲染真实卡片;
 * - 拖拽 = POST /views/{id}/moves 原子 move(乐观更新 + 409 收敛 + WIP 弹回 +
 *   跨项目预览确认,§4.3/§4.4);
 * - 实时增量合并(workspace:{ws}:issues + view:{id} 频道,单卡插入/移动/移除,
 *   禁整板刷新;view.updated/投影规则变更才重拉,§3.5);
 * - 重连/重放过期 → 「正在重新同步」横幅(§6.12 stale/resync)。
 *
 * 渲染序:无工作区空态 → 错误态(可重试)→ 骨架 → 视图空态(新建视图)→ 内容。
 * 选中视图 URL 同步 /views/{id}(§4.2 可分享/收藏)。
 */
/* eslint-disable react-refresh/only-export-components -- loadAllGroups 与页面组件同模块契约(测试复用) */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMatch, useNavigate, useParams } from 'react-router';
import { getApiClient } from '../../api/instance';
import { MeshApiError } from '../../api/errors';
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  Select,
  Skeleton,
  useToast,
} from '../../design';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { usePageContext, useShortcutRegistry } from '../../shortcuts';
import { useOptionalWorkspace } from '../../workspace/WorkspaceProvider';
import { createIssue, updateIssue, workspaceIssuesChannel } from '../issues/api';
import type { CreateIssueBody, IssuePriority } from '../issues/types';
import { fetchMe, listMembers } from '../members/api';
import type { MemberSummary, Membership } from '../members/types';
import { CREATE_ISSUE_PATH } from '../onboarding/deeplinks';
import { EmptyBoardColumns } from '../onboarding/illustrations';
import {
  createView,
  deleteView,
  duplicateView,
  listViews,
  setViewWip,
  updateView,
  viewChannel,
} from './api';
import { BoardColumns, computeDropPosition } from './BoardColumns';
import { BoardListView } from './BoardListView';
import { applyBoardFrame, cardBelongsToView, rebucketGroups } from './boardRealtime';
import { columnsForView, deriveColumns, isRenderableLayout } from './columns';
import { buildBoardGrid, columnKeyOfCard, moveCardSelection, nextColumnKey } from './keyboardNav';
import type { BoardDirection, BoardGrid } from './keyboardNav';
import { FilterConfigPanel } from './FilterConfigPanel';
import { fetchViewIssues, moveCard } from './projection';
import type { BoardCard, BoardGroup, MovePlan, ViewProjection } from './projection';
import { SortConfigPanel } from './SortConfigPanel';
import { ViewSaveBar } from './ViewSaveBar';
import { ViewSwitcher } from './ViewSwitcher';
import { WipConfigPanel } from './WipConfigPanel';
import type { BoardSettings, Filters, GroupByField, SortRule, View, WipEnforcement } from './types';
import './board.css';

type LoadStatus = 'loading' | 'ready' | 'empty' | 'error';

interface ViewDraft {
  readonly group_by: GroupByField | null;
  readonly sub_group_by: GroupByField | null;
  readonly filters: Filters;
  readonly sort: readonly SortRule[];
  readonly board_settings: BoardSettings;
}

function draftFromView(view: View): ViewDraft {
  return {
    group_by: view.group_by,
    sub_group_by: view.sub_group_by,
    filters: view.filters,
    sort: view.sort,
    board_settings: view.board_settings,
  };
}

function draftDiffers(view: View, draft: ViewDraft): boolean {
  return (
    draft.group_by !== view.group_by ||
    draft.sub_group_by !== view.sub_group_by ||
    JSON.stringify(draft.filters) !== JSON.stringify(view.filters) ||
    JSON.stringify(draft.sort) !== JSON.stringify(view.sort) ||
    JSON.stringify(draft.board_settings) !== JSON.stringify(view.board_settings)
  );
}

type PanelKey = 'filter' | 'sort' | 'wip' | 'display';

const GROUP_BY_OPTIONS: readonly GroupByField[] = [
  'state_category',
  'status',
  'assignee',
  'priority',
  'project',
  'label',
];

/** 新建卡片插入高亮保持时长(§9.3.4)。 */
const HIGHLIGHT_MS = 1200;

/** 从整体游标分组包络拉取整板卡片(遍历 next_cursor 至末页,§6.14)。 */
export async function loadAllGroups(
  client: ReturnType<typeof getApiClient>,
  viewId: string,
): Promise<ViewProjection> {
  const first = await fetchViewIssues(client, viewId, { limit: 200 });
  if (first.next_cursor === null) return first;
  const byKey = new Map<string, BoardGroup>();
  for (const group of first.groups) {
    byKey.set(group.key, group);
  }
  let cursor: string | null = first.next_cursor;
  while (cursor !== null) {
    const page = await fetchViewIssues(client, viewId, { limit: 200, cursor });
    for (const incoming of page.groups) {
      const existing = byKey.get(incoming.key);
      byKey.set(
        incoming.key,
        existing === undefined
          ? incoming
          : { ...existing, data: [...existing.data, ...incoming.data] },
      );
    }
    cursor = page.next_cursor;
  }
  return { ...first, groups: [...byKey.values()] };
}

export function BoardPage(): React.JSX.Element {
  const t = useT();
  const navigate = useNavigate();
  const { viewId } = useParams<{ viewId: string }>();
  const toast = useToast();
  const client = useMemo(() => getApiClient(), []);
  const realtime = useRealtimeContext();
  const workspaceContext = useOptionalWorkspace();
  const hasWorkspaceContext = workspaceContext !== null;
  const workspaceContextStatus = workspaceContext?.status ?? null;
  const contextWorkspace = workspaceContext?.workspace ?? null;

  // addToast 经 ref 持有:避免 toast 上下文对象每次渲染换引用而让回调失效,
  // 进而触发挂载 effect 在加载失败路径上无限重跑(§6.12 错误态)。
  const addToastRef = useRef(toast.addToast);
  addToastRef.current = toast.addToast;

  const [standaloneMembership, setStandaloneMembership] = useState<Membership | null>(null);
  const [standaloneWsStatus, setStandaloneWsStatus] = useState<LoadStatus>('loading');
  // 生产路由由 AppShell 挂载 WorkspaceProvider；视图、创建、订阅与深链均以该
  // provider 当前 workspace 为唯一真源。Provider 外兼容仅供独立测试/嵌入渲染。
  const providerMembership = useMemo<Membership | null>(() => {
    if (workspaceContextStatus !== 'ready' || contextWorkspace === null) return null;
    const current = contextWorkspace;
    return {
      workspace_id: current.id,
      workspace_name: current.name,
      workspace_slug: current.slug,
      role: current.my_role,
      status: 'active',
      joined_at: null,
    };
  }, [workspaceContextStatus, contextWorkspace]);
  const membership = hasWorkspaceContext ? providerMembership : standaloneMembership;
  const wsStatus: LoadStatus = hasWorkspaceContext
    ? workspaceContextStatus === 'loading'
      ? 'loading'
      : workspaceContextStatus === 'ready'
        ? providerMembership === null
          ? 'empty'
          : 'ready'
        : workspaceContextStatus === 'not_found'
          ? 'empty'
          : 'error'
    : standaloneWsStatus;
  const [views, setViews] = useState<readonly View[]>([]);
  const [viewsStatus, setViewsStatus] = useState<LoadStatus>('loading');
  const [draft, setDraft] = useState<ViewDraft | null>(null);
  const [panel, setPanel] = useState<PanelKey | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState('');

  // 投影层状态:整板分组 + 列目标状态映射 + 加载态。
  const [boardGroups, setBoardGroups] = useState<readonly BoardGroup[]>([]);
  const [columnTargetStatus, setColumnTargetStatus] = useState<Readonly<Record<string, string>>>(
    {},
  );
  const [boardStatus, setBoardStatus] = useState<LoadStatus>('loading');
  const [resyncing, setResyncing] = useState(false);
  const [movePreview, setMovePreview] = useState<{
    plan: MovePlan;
    issueId: string;
    toGroupKey: string;
    position: number;
    version: number;
  } | null>(null);

  // 新建卡片 1.2s 插入高亮(§9.3.4):创建成功并重拉后闪烁新卡。
  const [highlightCardId, setHighlightCardId] = useState<string | null>(null);
  const highlightTimerRef = useRef<number | null>(null);
  const flashHighlight = useCallback((cardId: string) => {
    setHighlightCardId(cardId);
    if (highlightTimerRef.current !== null) window.clearTimeout(highlightTimerRef.current);
    highlightTimerRef.current = window.setTimeout(() => setHighlightCardId(null), HIGHLIGHT_MS);
  }, []);

  const boardGroupsRef = useRef(boardGroups);
  boardGroupsRef.current = boardGroups;

  // —— 键盘流转(§4.3 S10 / 评审 P4):选中态 + 二维网格移动 + 上下文组注册 ——
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);
  const selectedCardIdRef = useRef(selectedCardId);
  selectedCardIdRef.current = selectedCardId;
  const boardGridRef = useRef<BoardGrid>([]);
  const membersCacheRef = useRef<readonly MemberSummary[] | null>(null);

  // 规范路由前缀:挂载于 /w/{slug}/* 时内部导航保持 workspace-scoped 规范形态。
  const workspaceRouteMatch = useMatch('/w/:workspaceSlug/*');
  const routeWorkspaceSlug = workspaceRouteMatch?.params.workspaceSlug;
  const canonicalWorkspaceSlug = providerMembership?.workspace_slug ?? routeWorkspaceSlug;
  const routePrefix =
    canonicalWorkspaceSlug !== undefined ? `/w/${encodeURIComponent(canonicalWorkspaceSlug)}` : '';
  const routePrefixRef = useRef(routePrefix);
  routePrefixRef.current = routePrefix;

  // 看板上下文激活:['global','board'](卸载复位 ['global'],setContexts 死代码接通)。
  usePageContext('board');

  // 键盘动作实现经 ref 间接:注册 effect 仅挂载时执行一次,动作闭包随渲染更新。
  const boardActionsRef = useRef({
    move: (_direction: BoardDirection): void => undefined,
    newCard: (): void => undefined,
    changeStatus: (): void => undefined,
    changeAssignee: (): void => undefined,
    openCard: (): void => undefined,
    toggleFilter: (): void => undefined,
  });

  useEffect(() => {
    const registry = useShortcutRegistry.getState();
    const actions = () => boardActionsRef.current;
    const move = (direction: BoardDirection) => () => actions().move(direction);
    return registry.registerShortcuts([
      {
        id: 'board.move.up',
        combo: 'arrowup',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('up'),
      },
      {
        id: 'board.move.down',
        combo: 'arrowdown',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('down'),
      },
      {
        id: 'board.move.left',
        combo: 'arrowleft',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('left'),
      },
      {
        id: 'board.move.right',
        combo: 'arrowright',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('right'),
      },
      {
        id: 'board.move.up.vim',
        combo: 'k',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('up'),
      },
      {
        id: 'board.move.down.vim',
        combo: 'j',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('down'),
      },
      {
        id: 'board.move.left.vim',
        combo: 'h',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('left'),
      },
      {
        id: 'board.move.right.vim',
        combo: 'l',
        label: t('shortcuts.boardMove'),
        group: 'board',
        run: move('right'),
      },
      // §4.3.1 规则 3:看板 C 仲裁胜出于全局 C(复用列快速创建并预填当前列)。
      {
        id: 'board.new.card',
        combo: 'c',
        label: t('shortcuts.boardNewCard'),
        group: 'board',
        run: () => actions().newCard(),
      },
      {
        id: 'board.change.status',
        combo: 's',
        label: t('shortcuts.boardChangeStatus'),
        group: 'board',
        run: () => actions().changeStatus(),
      },
      {
        id: 'board.change.assignee',
        combo: 'a',
        label: t('shortcuts.boardChangeAssignee'),
        group: 'board',
        run: () => actions().changeAssignee(),
      },
      {
        id: 'board.open.card',
        combo: 'enter',
        label: t('shortcuts.boardOpenCard'),
        group: 'board',
        run: () => actions().openCard(),
      },
      {
        id: 'board.filter',
        combo: 'f',
        label: t('shortcuts.boardFilter'),
        group: 'board',
        run: () => actions().toggleFilter(),
      },
    ]);
  }, [t]);

  // 投影加载竞态防护(验收必修 1):loadSeq 单调递增,响应写回前校验序号与当前
  // 视图 id——切换视图后,旧视图的在途/分页响应一律丢弃,不得覆盖新视图数据。
  const loadSeqRef = useRef(0);
  const selectedViewIdRef = useRef<string | null>(null);
  // 视图加载键(id + 投影相关配置):views 列表 refetch 使选中视图对象换新引用但
  // 内容未变时不重载(杜绝向旧视图 issues 端点发多余请求);配置变更(保存视图)
  // 键变 → 正常重载。
  const lastLoadedKeyRef = useRef<string | null>(null);

  // 当前生效分组(草稿 group_by;须在早期返回之前的 hooks 区计算,§ Rules of Hooks)。
  const effectiveGroupBy = draft?.group_by ?? 'state_category';
  // 已加载卡片按生效 group_by 本地重分桶(§4.2 分组切换即时反映);
  // 组标签由服务端按工作区 locale 本地化后下发(含无负责人/无项目列),客户端不再硬编码。
  const displayGroups = useMemo(
    () => rebucketGroups(boardGroups, effectiveGroupBy),
    [boardGroups, effectiveGroupBy],
  );

  const toastError = useCallback(
    (error: unknown) => {
      const message =
        error instanceof MeshApiError ? `${t('error.' + error.code)}` : t('common.unknownError');
      addToastRef.current(message, { tone: 'danger', closeLabel: t('common.close') });
    },
    [t],
  );

  const loadStandaloneWorkspace = useCallback(async () => {
    setStandaloneWsStatus('loading');
    try {
      const me = await fetchMe(client);
      // Provider 外不得猜首项:有 scoped URL 时按 slug 精确匹配；无 scoped URL
      // 仅接受唯一 membership（现有独立组件测试均为该形态）。
      const active =
        routeWorkspaceSlug !== undefined
          ? (me.memberships.find(
              (membership) => membership.workspace_slug === routeWorkspaceSlug,
            ) ?? null)
          : me.memberships.length === 1
            ? (me.memberships.find(() => true) ?? null)
            : null;
      setStandaloneMembership(active);
      setStandaloneWsStatus(active === null ? 'empty' : 'ready');
    } catch (error) {
      setStandaloneWsStatus('error');
      toastError(error);
    }
  }, [client, routeWorkspaceSlug, toastError]);

  const currentWorkspaceIdRef = useRef<string | null>(membership?.workspace_id ?? null);
  currentWorkspaceIdRef.current = membership?.workspace_id ?? null;
  const viewsLoadSeqRef = useRef(0);

  const loadViews = useCallback(
    async (workspaceId: string) => {
      const seq = ++viewsLoadSeqRef.current;
      setViewsStatus('loading');
      try {
        const page = await listViews(client, workspaceId, { limit: 100 });
        if (seq !== viewsLoadSeqRef.current || currentWorkspaceIdRef.current !== workspaceId) {
          return;
        }
        setViews(page.data);
        setViewsStatus(page.data.length === 0 ? 'empty' : 'ready');
      } catch (error) {
        if (seq !== viewsLoadSeqRef.current || currentWorkspaceIdRef.current !== workspaceId) {
          return;
        }
        setViewsStatus('error');
        toastError(error);
      }
    },
    [client, toastError],
  );

  useEffect(() => {
    if (!hasWorkspaceContext) void loadStandaloneWorkspace();
  }, [hasWorkspaceContext, loadStandaloneWorkspace]);

  const currentWorkspaceId = membership?.workspace_id ?? null;
  useEffect(() => {
    viewsLoadSeqRef.current += 1;
    setViews([]);
    membersCacheRef.current = null;
    if (currentWorkspaceId !== null) void loadViews(currentWorkspaceId);
  }, [currentWorkspaceId, loadViews]);

  const scopedViews = useMemo(
    () =>
      currentWorkspaceId === null
        ? []
        : views.filter((view) => view.workspace_id === currentWorkspaceId),
    [views, currentWorkspaceId],
  );

  const selectedView = useMemo(() => {
    if (scopedViews.length === 0) return null;
    if (viewId !== undefined) {
      const match = scopedViews.find((view) => view.id === viewId);
      if (match !== undefined) return match;
    }
    return scopedViews.find((view) => view.is_default) ?? scopedViews[0] ?? null;
  }, [scopedViews, viewId]);
  selectedViewIdRef.current = selectedView?.id ?? null;

  useEffect(() => {
    setDraft(selectedView === null ? null : draftFromView(selectedView));
  }, [selectedView]);

  const selectView = useCallback(
    (nextId: string) => {
      navigate(`${routePrefixRef.current}/views/${encodeURIComponent(nextId)}`);
    },
    [navigate],
  );

  // 投影加载:选中视图变化 → 执行视图配置拉取整板(§3.2)。
  // 已有内容时保持渲染(§10.1/§13.3 局部刷新不清空已有内容):快速创建/移动后
  // 的整板重拉不卸载 BoardColumns,紧凑模式当前列与滚动位置得以保持;
  // 切换视图时由下方 effect 先清空分组,走骨架屏路径。
  const loadBoard = useCallback(
    async (view: View) => {
      const seq = ++loadSeqRef.current;
      if (boardGroupsRef.current.length === 0) setBoardStatus('loading');
      try {
        const projection = await loadAllGroups(client, view.id);
        // 过期写回防护:加载期间视图已切换或已有更新的加载发起 → 丢弃结果。
        if (seq !== loadSeqRef.current || selectedViewIdRef.current !== view.id) return;
        setBoardGroups(projection.groups);
        setColumnTargetStatus(projection.column_target_status);
        setBoardStatus('ready');
      } catch (error) {
        if (seq !== loadSeqRef.current || selectedViewIdRef.current !== view.id) return;
        setBoardStatus('error');
        toastError(error);
      }
    },
    [client, toastError],
  );

  useEffect(() => {
    // board 与 list 布局均为可渲染投影(§3.2 逐页差距:list 为真实表格布局,
    // 不再是占位空态),选中视图变化 → 拉取投影。切换视图时使旧加载失效
    // (loadSeq 递增于 loadBoard)并清空分组、立即置 loading,避免短暂展示
    // 上一视图数据(§9.7 同类约束)。timeline/table 未实现 → 清空走占位分支。
    if (selectedView !== null && isRenderableLayout(selectedView.layout)) {
      const key =
        selectedView.id +
        JSON.stringify([
          selectedView.layout,
          selectedView.group_by,
          selectedView.sub_group_by,
          selectedView.filters,
          selectedView.sort,
          selectedView.board_settings,
        ]);
      if (lastLoadedKeyRef.current === key) return; // 同视图同配置(列表 refetch 换引用):不重载
      lastLoadedKeyRef.current = key;
      setBoardGroups([]);
      setBoardStatus('loading');
      void loadBoard(selectedView);
    } else {
      loadSeqRef.current += 1; // 使任何在途加载失效
      lastLoadedKeyRef.current = null;
      setBoardGroups([]);
      setBoardStatus('ready');
    }
  }, [selectedView, loadBoard]);

  // 实时增量合并(§3.5):订阅工作区 issue 频道 + 视图频道,单卡插入/移动/移除。
  const filtersRef = useRef<Filters>(selectedView?.filters ?? {});
  filtersRef.current = selectedView?.filters ?? {};
  useEffect(() => {
    if (realtime === null || selectedView === null || membership === null) return;
    // board 与 list 布局均订阅增量合并(§3.5):list 同样是投影视图,
    // issue.* 帧按 filters 重判归属,单卡插入/移动/移除,refetch 帧重拉。
    if (!isRenderableLayout(selectedView.layout)) return;
    const wsChannel = workspaceIssuesChannel(membership.workspace_id);
    const vChannel = viewChannel(selectedView.id);
    realtime.client.subscribe(wsChannel);
    realtime.client.subscribe(vChannel);
    const offFrame = realtime.client.onFrame((frame) => {
      if (frame.channel !== wsChannel && frame.channel !== vChannel) return;
      // 视图切换后晚到的帧属过期闭包:跳过(新视图订阅随即接管),
      // 杜绝以旧视图 id 发起多余投影请求(验收必修 1 竞态收口)。
      if (selectedViewIdRef.current !== selectedView.id) return;
      if (frame.event === 'view.presence') return;
      // §4.4/§5.1: warn 超限放行后,服务端广播 view.wip_exceeded → 顶部 toast
      // (拖拽者本人与同视图协作者均可见),与列头红色徽章并存。
      if (frame.event === 'view.wip_exceeded') {
        const d = frame.payload as { group_key?: unknown; limit?: unknown; count?: unknown };
        addToastRef.current(
          t('board.wipExceededToast', {
            group: String(d.group_key ?? ''),
            limit: d.limit ?? 0,
            count: d.count ?? 0,
          }),
          { tone: 'warn', closeLabel: t('common.close') },
        );
      }
      const result = applyBoardFrame(boardGroupsRef.current, frame, {
        groupBy: selectedView.group_by ?? 'state_category',
        belongs: (card) => cardBelongsToView(card, filtersRef.current),
      });
      if (result.refetch) {
        void loadBoard(selectedView);
      } else {
        setBoardGroups(result.groups);
      }
    });
    const offState = realtime.client.onState((state) => {
      setResyncing(state === 'resyncing' || state === 'reconnecting');
    });
    return () => {
      offFrame();
      offState();
      realtime.client.unsubscribe(wsChannel);
      realtime.client.unsubscribe(vChannel);
    };
  }, [realtime, selectedView, membership, loadBoard, t]);

  if (wsStatus === 'loading') {
    return (
      <div className="mesh-board" data-testid="board-page">
        <Skeleton loadingLabel={t('common.loading')} className="mesh-board__skeleton" />
      </div>
    );
  }
  if (wsStatus === 'error') {
    return (
      <div className="mesh-board" data-testid="board-page">
        <ErrorState
          title={t('state.errorTitle')}
          description={t('state.errorDescription')}
          retryLabel={t('common.retry')}
          onRetry={() => {
            if (workspaceContext === null) void loadStandaloneWorkspace();
            else void workspaceContext.refresh();
          }}
        />
      </div>
    );
  }
  if (wsStatus === 'empty' || membership === null) {
    return (
      <div className="mesh-board" data-testid="board-page">
        <EmptyState
          title={t('board.noWorkspaceTitle')}
          description={t('board.noWorkspaceDescription')}
        />
      </div>
    );
  }

  if (viewsStatus === 'loading') {
    return (
      <div className="mesh-board" data-testid="board-page">
        <Skeleton loadingLabel={t('common.loading')} className="mesh-board__skeleton" />
      </div>
    );
  }
  if (viewsStatus === 'error') {
    return (
      <div className="mesh-board" data-testid="board-page">
        <ErrorState
          title={t('state.errorTitle')}
          description={t('state.errorDescription')}
          retryLabel={t('common.retry')}
          onRetry={() => void loadViews(membership.workspace_id)}
        />
      </div>
    );
  }

  const workspaceId = membership.workspace_id;

  const handleCreate = async (
    name: string,
    layout: View['layout'],
    visibility: View['visibility'],
  ): Promise<void> => {
    try {
      const created = await createView(client, workspaceId, { name, layout, visibility });
      await loadViews(workspaceId);
      selectView(created.id);
    } catch (error) {
      toastError(error);
      throw error;
    }
  };

  const handleRename = async (view: View, name: string): Promise<void> => {
    try {
      await updateView(client, view.id, { name }, { ifMatch: view.updated_at });
      await loadViews(workspaceId);
    } catch (error) {
      toastError(error);
    }
  };

  const handleDuplicate = async (view: View): Promise<void> => {
    try {
      const copy = await duplicateView(client, view.id);
      await loadViews(workspaceId);
      selectView(copy.id);
    } catch (error) {
      toastError(error);
    }
  };

  const handleSetDefault = async (view: View): Promise<void> => {
    try {
      await updateView(client, view.id, { is_default: true }, { ifMatch: view.updated_at });
      await loadViews(workspaceId);
    } catch (error) {
      toastError(error);
    }
  };

  const handleDelete = async (view: View): Promise<void> => {
    try {
      await deleteView(client, view.id);
      await loadViews(workspaceId);
      if (selectedView?.id === view.id) {
        navigate(`${routePrefixRef.current}/board`);
      }
    } catch (error) {
      toastError(error);
    }
  };

  if (viewsStatus === 'empty' || selectedView === null || draft === null) {
    return (
      <div className="mesh-board" data-testid="board-page">
        <ViewSwitcher
          views={scopedViews}
          selectedId={null}
          canWrite={(view) => view.can_write === true}
          onSelect={selectView}
          onCreate={handleCreate}
          onRename={handleRename}
          onDuplicate={handleDuplicate}
          onSetDefault={handleSetDefault}
          onDelete={handleDelete}
        />
        <EmptyState
          illustration={<EmptyBoardColumns />}
          title={t('onboarding.empty.board.title')}
          description={t('onboarding.empty.board.description')}
          action={
            <div className="mesh-board__empty-actions">
              {/* 主操作:深链既有 issue 快速创建(命令面板 / 快捷键 c 同路径,§6.12) */}
              <Button
                variant="primary"
                data-testid="board-empty-new-issue"
                onClick={() => navigate(`${routePrefix}${CREATE_ISSUE_PATH}`)}
              >
                {t('onboarding.empty.board.action')}
              </Button>
              {/* 次操作:新建视图(原入口保留) */}
              <Button
                variant="secondary"
                data-testid="board-empty-create"
                onClick={() =>
                  document
                    .querySelector<HTMLButtonElement>('[data-testid="view-create-open"]')
                    ?.click()
                }
              >
                + {t('board.newView')}
              </Button>
            </div>
          }
        />
      </div>
    );
  }

  const dirty = draftDiffers(selectedView, draft);
  const canWrite = selectedView.can_write === true;
  const previewView: View = {
    ...selectedView,
    group_by: draft.group_by,
    sub_group_by: draft.sub_group_by,
    filters: draft.filters,
    sort: draft.sort,
    board_settings: draft.board_settings,
  };
  // 投影层:已加载卡片按「草稿 group_by」本地重分桶(displayGroups,见上方 hooks),
  // 再与列骨架合并(§3.2)。拖拽仅在非脏态启用(此时草稿=已保存,与服务端一致)。
  const derived = deriveColumns(previewView, displayGroups);
  const columns =
    selectedView.layout === 'board' && boardStatus === 'ready'
      ? derived.columns
      : columnsForView(previewView);
  const cardsByKey = derived.cardsByKey;

  // —— 键盘动作实现(§4.3 S10):注册 effect 经 boardActionsRef 间接调用 ——
  boardGridRef.current = buildBoardGrid(columns, cardsByKey);

  const focusCard = (cardId: string): void => {
    document.querySelector<HTMLElement>(`[data-testid="board-card-${cardId}"]`)?.focus();
  };

  const keyboardMove = (direction: BoardDirection): void => {
    const next = moveCardSelection(boardGridRef.current, selectedCardIdRef.current, direction);
    if (next === null) return; // 全空列保持原选中并忽略
    setSelectedCardId(next);
    focusCard(next);
  };

  // C:当前选中列快速创建(预填该列分组值,§4.3.1 规则 3 同一创建的两种预填形态);
  // 无可用列回退全局新建(空弹窗 /issues?create=1)。
  const keyboardNewCard = (): void => {
    const grid = boardGridRef.current;
    const targetKey =
      selectedCardIdRef.current !== null
        ? columnKeyOfCard(grid, selectedCardIdRef.current)
        : (grid[0]?.key ?? null);
    if (targetKey === null) {
      navigate(`${routePrefixRef.current}/issues?create=1`);
      return;
    }
    document.querySelector<HTMLElement>(`[data-testid="quick-add-${targetKey}"]`)?.focus();
  };

  // S:改选中卡状态 —— 复用列 UI 的 move API(下一列循环推进,等价鼠标拖拽路径)。
  const keyboardChangeStatus = (): void => {
    const grid = boardGridRef.current;
    const cardId = selectedCardIdRef.current;
    if (cardId === null || selectedView === null) return;
    const fromKey = columnKeyOfCard(grid, cardId);
    if (fromKey === null) return;
    const toKey = nextColumnKey(grid, fromKey);
    if (toKey === null || toKey === fromKey) return;
    const targetCell = grid.find((cell) => cell.key === toKey);
    void handleDropCard(cardId, toKey, computeDropPosition(targetCell?.cards ?? [], null));
  };

  // A:改选中卡负责人 —— 成员名册循环切换(等价鼠标路径:issue 详情负责人选择)。
  const keyboardChangeAssignee = (): void => {
    const cardId = selectedCardIdRef.current;
    if (cardId === null) return;
    const card = boardGroupsRef.current
      .flatMap((group) => group.data)
      .find((item) => item.id === cardId);
    if (card === undefined) return;
    void (async () => {
      try {
        let members = membersCacheRef.current;
        if (members === null) {
          const page = await listMembers(client, workspaceId, { limit: 100 });
          members = page.data;
          membersCacheRef.current = members;
        }
        if (members.length === 0) return;
        const idx = members.findIndex((member) => member.id === card.assignee_id);
        const next = members[(idx + 1) % members.length];
        if (next === undefined) return;
        await updateIssue(client, card.id, { assignee_id: next.id });
        if (selectedView !== null) await loadBoard(selectedView);
      } catch (error) {
        toastError(error);
      }
    })();
  };

  const keyboardOpenCard = (): void => {
    const cardId = selectedCardIdRef.current;
    if (cardId === null) return;
    navigate(`${routePrefixRef.current}/issues/${encodeURIComponent(cardId)}`);
  };

  boardActionsRef.current = {
    move: keyboardMove,
    newCard: keyboardNewCard,
    changeStatus: keyboardChangeStatus,
    changeAssignee: keyboardChangeAssignee,
    openCard: keyboardOpenCard,
    toggleFilter: () => setPanel((current) => (current === 'filter' ? null : 'filter')),
  };

  const toggleCollapse = (key: string): void => {
    const collapsed = new Set(draft.board_settings.collapsed_columns ?? []);
    if (collapsed.has(key)) {
      collapsed.delete(key);
    } else {
      collapsed.add(key);
    }
    setDraft({
      ...draft,
      board_settings: { ...draft.board_settings, collapsed_columns: [...collapsed] },
    });
  };

  const handleSave = async (): Promise<void> => {
    setBusy(true);
    try {
      await updateView(
        client,
        selectedView.id,
        {
          group_by: draft.group_by,
          sub_group_by: draft.sub_group_by,
          filters: draft.filters,
          sort: [...draft.sort],
          board_settings: draft.board_settings,
        },
        { ifMatch: selectedView.updated_at },
      );
      await loadViews(workspaceId);
    } catch (error) {
      if (error instanceof MeshApiError && error.code === 'conflict') {
        await loadViews(workspaceId);
      }
      toastError(error);
    } finally {
      setBusy(false);
    }
  };

  const handleSaveAs = async (): Promise<void> => {
    const name = saveAsName.trim();
    if (name === '') return;
    setBusy(true);
    try {
      const created = await createView(client, workspaceId, {
        name,
        layout: selectedView.layout,
        visibility: selectedView.visibility,
        project_id: selectedView.project_id,
        group_by: draft.group_by,
        sub_group_by: draft.sub_group_by,
        filters: draft.filters,
        sort: [...draft.sort],
        board_settings: draft.board_settings,
      });
      setSaveAsOpen(false);
      setSaveAsName('');
      await loadViews(workspaceId);
      selectView(created.id);
    } catch (error) {
      toastError(error);
    } finally {
      setBusy(false);
    }
  };

  const handleWipSave = async (
    groupKey: string,
    limit: number | null,
    enforcement: WipEnforcement,
  ): Promise<void> => {
    try {
      await setViewWip(client, selectedView.id, { group_key: groupKey, limit, enforcement });
      await loadViews(workspaceId);
      await loadBoard(selectedView);
    } catch (error) {
      toastError(error);
    }
  };

  // 拖拽原子 move(§4.3/§4.4):乐观落位 → 服务端 move → 失败收敛/弹回。
  const moveCardInGroups = (
    groups: readonly BoardGroup[],
    issueId: string,
    toGroupKey: string,
    position: number,
    patch: Partial<BoardCard>,
  ): BoardGroup[] => {
    let moved: BoardCard | null = null;
    const stripped = groups.map((group) => {
      const card = group.data.find((item) => item.id === issueId);
      if (card === undefined) return group;
      moved = card;
      return {
        ...group,
        count: Math.max(0, group.count - 1),
        data: group.data.filter((item) => item.id !== issueId),
      };
    });
    if (moved === null) return groups as BoardGroup[];
    const updated: BoardCard = { ...(moved as BoardCard), ...patch, position };
    return stripped.map((group) =>
      group.key === toGroupKey
        ? { ...group, count: group.count + 1, data: [...group.data, updated] }
        : group,
    );
  };

  const targetPatchFor = (toGroupKey: string): Partial<BoardCard> => {
    const groupBy = effectiveGroupBy;
    if (groupBy === 'status') {
      return { status_id: toGroupKey };
    }
    if (groupBy === 'assignee') {
      return { assignee_id: toGroupKey === '__none__' ? null : toGroupKey };
    }
    if (groupBy === 'priority') {
      return { priority: toGroupKey };
    }
    if (groupBy === 'project') {
      return { project_id: toGroupKey === '__none__' ? null : toGroupKey };
    }
    // state_category:状态改为该列默认 status(§2.4 column_target_status)。
    const statusId = columnTargetStatus[toGroupKey];
    return {
      state_category: toGroupKey,
      status_id: statusId ?? undefined,
      status:
        statusId !== undefined
          ? { id: statusId, name: toGroupKey, category: toGroupKey }
          : undefined,
    };
  };

  const handleDropCard = async (
    issueId: string,
    toGroupKey: string,
    position: number,
  ): Promise<void> => {
    if (selectedView === null) return;
    const snapshot = boardGroupsRef.current;
    const card = snapshot.flatMap((group) => group.data).find((item) => item.id === issueId);
    if (card === undefined) return;

    // 乐观落位(§4.3)。
    setBoardGroups(
      moveCardInGroups(snapshot, issueId, toGroupKey, position, targetPatchFor(toGroupKey)),
    );

    try {
      const result = await moveCard(client, selectedView.id, {
        issue_id: issueId,
        to_group_key: toGroupKey,
        position,
        version: card.version,
      });
      // 用服务端最新字段/版本收敛。
      setBoardGroups((current) => moveCardInGroups(current, issueId, toGroupKey, position, result));
    } catch (error) {
      if (error instanceof MeshApiError && error.code === 'move_confirmation_required') {
        // 跨项目拖拽:弹回 + 展示迁移预览要求确认(§4.3/T22)。
        setBoardGroups(snapshot);
        const plan = (error.details?.preview ?? {}) as MovePlan;
        setMovePreview({ plan, issueId, toGroupKey, position, version: card.version });
        return;
      }
      // WIP block / 其它失败 → 弹回原列 + 提示(§4.4)。
      setBoardGroups(snapshot);
      if (error instanceof MeshApiError && error.code === 'conflict') {
        // 409 → 拉最新静默收敛(§4.3/§5.2:后到事件覆盖,多人同拖同卡平滑收敛,
        // 不 toast 噪音;浏览器网络层 409 日志属已处理冲突,非应用错误)。
        await loadBoard(selectedView);
        return;
      }
      toastError(error);
    }
  };

  const confirmMove = async (): Promise<void> => {
    if (movePreview === null || selectedView === null) return;
    const { issueId, toGroupKey, position, version } = movePreview;
    setMovePreview(null);
    try {
      const result = await moveCard(client, selectedView.id, {
        issue_id: issueId,
        to_group_key: toGroupKey,
        position,
        version,
        confirm: true,
      });
      setBoardGroups((current) => moveCardInGroups(current, issueId, toGroupKey, position, result));
    } catch (error) {
      if (error instanceof MeshApiError && error.code === 'conflict') {
        await loadBoard(selectedView);
      }
      toastError(error);
    }
  };

  const handleQuickCreate = async (groupKey: string, title: string): Promise<void> => {
    if (selectedView === null) return;
    const groupBy = effectiveGroupBy;
    // 继承该列分组值(§4.5)。
    const inherited: Partial<CreateIssueBody> =
      groupBy === 'status'
        ? { status_id: groupKey }
        : groupBy === 'state_category'
          ? { status_id: columnTargetStatus[groupKey] }
          : groupBy === 'priority'
            ? { priority: groupKey as IssuePriority }
            : groupBy === 'assignee'
              ? { assignee_id: groupKey === '__none__' ? null : groupKey }
              : groupBy === 'project'
                ? { project_id: groupKey === '__none__' ? null : groupKey }
                : {};
    try {
      const created = await createIssue(client, workspaceId, { title, ...inherited });
      await loadBoard(selectedView);
      flashHighlight(created.id);
    } catch (error) {
      toastError(error);
    }
  };

  return (
    <div className="mesh-board" data-testid="board-page">
      <ViewSwitcher
        views={scopedViews}
        selectedId={selectedView.id}
        canWrite={(view) => view.can_write === true}
        onSelect={selectView}
        onCreate={handleCreate}
        onRename={handleRename}
        onDuplicate={handleDuplicate}
        onSetDefault={handleSetDefault}
        onDelete={handleDelete}
      />
      <div className="mesh-board__main">
        <header className="mesh-board__toolbar">
          <h1 className="mesh-board__title" data-testid="board-title">
            {selectedView.name}
          </h1>
          <span className="mesh-board__layout-chip">
            {t('board.layout.' + selectedView.layout)}
          </span>
          <Select
            label={t('board.groupByLabel')}
            value={draft.group_by ?? 'state_category'}
            disabled={!canWrite}
            onChange={(event) =>
              setDraft({ ...draft, group_by: event.target.value as GroupByField })
            }
            data-testid="group-by-select"
          >
            {GROUP_BY_OPTIONS.map((field) => (
              <option key={field} value={field}>
                {t('board.groupBy.' + field)}
              </option>
            ))}
          </Select>
          <Select
            label={t('board.subGroupByLabel')}
            value={draft.sub_group_by ?? ''}
            disabled={!canWrite}
            onChange={(event) =>
              setDraft({
                ...draft,
                sub_group_by:
                  event.target.value === '' ? null : (event.target.value as GroupByField),
              })
            }
            data-testid="sub-group-by-select"
          >
            <option value="">{t('board.subGroupNone')}</option>
            {GROUP_BY_OPTIONS.map((field) => (
              <option key={field} value={field}>
                {t('board.groupBy.' + field)}
              </option>
            ))}
          </Select>
          <div className="mesh-board__toolbar-actions">
            {(
              [
                ['filter', t('board.filterButton')],
                ['sort', t('board.sortButton')],
                ['wip', t('board.wipButton')],
              ] as const
            ).map(([key, label]) => (
              <Button
                key={key}
                variant={panel === key ? 'primary' : 'secondary'}
                disabled={!canWrite}
                aria-expanded={panel === key}
                data-testid={`panel-toggle-${key}`}
                onClick={() => setPanel(panel === key ? null : key)}
              >
                {label}
              </Button>
            ))}
          </div>
        </header>

        {resyncing ? (
          <div className="mesh-board__resync" role="status" data-testid="board-resync-banner">
            {t('board.resyncing')}
          </div>
        ) : null}

        <ViewSaveBar
          dirty={dirty}
          busy={busy}
          canWrite={canWrite}
          onSave={() => void handleSave()}
          onSaveAs={() => {
            setSaveAsName(`${selectedView.name} (copy)`);
            setSaveAsOpen(true);
          }}
          onDiscard={() => setDraft(draftFromView(selectedView))}
        />

        {panel === 'filter' ? (
          <FilterConfigPanel
            filters={draft.filters}
            onChange={(filters) => setDraft({ ...draft, filters })}
          />
        ) : null}
        {panel === 'sort' ? (
          <SortConfigPanel rules={draft.sort} onChange={(sort) => setDraft({ ...draft, sort })} />
        ) : null}
        {panel === 'wip' ? <WipConfigPanel columns={columns} onSave={handleWipSave} /> : null}

        {selectedView.layout === 'board' ? (
          boardStatus === 'loading' ? (
            <Skeleton loadingLabel={t('common.loading')} className="mesh-board__skeleton" />
          ) : boardStatus === 'error' ? (
            <ErrorState
              title={t('state.errorTitle')}
              description={t('state.errorDescription')}
              retryLabel={t('common.retry')}
              onRetry={() => void loadBoard(selectedView)}
            />
          ) : (
            <BoardColumns
              columns={columns}
              groupBy={effectiveGroupBy}
              cardsByKey={cardsByKey}
              canWrite={canWrite}
              dragEnabled={canWrite && !dirty}
              onToggleCollapse={toggleCollapse}
              onDropCard={(issueId, toGroupKey, position) =>
                void handleDropCard(issueId, toGroupKey, position)
              }
              onQuickCreate={(groupKey, title) => handleQuickCreate(groupKey, title)}
              highlightCardId={highlightCardId}
            />
          )
        ) : selectedView.layout === 'list' ? (
          boardStatus === 'loading' ? (
            <Skeleton loadingLabel={t('common.loading')} className="mesh-board__skeleton" />
          ) : boardStatus === 'error' ? (
            <ErrorState
              title={t('state.errorTitle')}
              description={t('state.errorDescription')}
              retryLabel={t('common.retry')}
              onRetry={() => void loadBoard(selectedView)}
            />
          ) : (
            <BoardListView
              view={previewView}
              groups={displayGroups}
              columnTargetStatus={columnTargetStatus}
              canWrite={canWrite}
              onOpenIssue={(id: string) =>
                navigate(`${routePrefixRef.current}/issues/${encodeURIComponent(id)}`)
              }
              onChanged={() => void loadBoard(selectedView)}
            />
          )
        ) : (
          <EmptyState
            title={t('board.notImplementedTitle')}
            description={t('board.notImplementedDescription')}
          />
        )}
      </div>

      <Dialog
        open={saveAsOpen}
        onClose={() => setSaveAsOpen(false)}
        title={t('board.saveAsTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-board__dialog">
          <Input
            label={t('board.viewNameLabel')}
            value={saveAsName}
            maxLength={100}
            onChange={(event) => setSaveAsName(event.target.value)}
            data-testid="save-as-name"
          />
          <div className="mesh-board__dialog-actions">
            <Button variant="secondary" onClick={() => setSaveAsOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => void handleSaveAs()}
              disabled={saveAsName.trim() === '' || busy}
              data-testid="save-as-submit"
            >
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={movePreview !== null}
        onClose={() => setMovePreview(null)}
        title={t('board.movePreviewTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-board__dialog" data-testid="move-preview-dialog">
          <p>{t('board.movePreviewDescription')}</p>
          {movePreview !== null && movePreview.plan.mapped_fields.length > 0 ? (
            <p data-testid="move-preview-mapped">
              {t('board.movePreviewMapped', { count: movePreview.plan.mapped_fields.length })}
            </p>
          ) : null}
          {movePreview !== null && movePreview.plan.cleared_fields.length > 0 ? (
            <p data-testid="move-preview-cleared">
              {t('board.movePreviewCleared', { count: movePreview.plan.cleared_fields.length })}
            </p>
          ) : null}
          <div className="mesh-board__dialog-actions">
            <Button variant="secondary" onClick={() => setMovePreview(null)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void confirmMove()} data-testid="move-preview-confirm">
              {t('board.moveConfirm')}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
