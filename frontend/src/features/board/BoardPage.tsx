/**
 * 看板页面 shell(kanban.md §4.1/§4.2,README §6.12 异常态矩阵)。
 *
 * 定义层静态切片(MES-43):视图切换器 + 工具条(分组/筛选/排序/WIP/显示字段
 * 配置)+ 按 group_by 配置派生的列骨架;不接真实 issue 数据 —— 列体按 §6.12
 * 空态呈现,投影查询属 issue 耦合增量(MES-33 余量)。
 *
 * 渲染序:无工作区空态 → 错误态(可重试)→ 骨架 → 视图空态(主操作:新建视图)
 * → 内容(对齐 ProjectsPage)。URL 同步 /board/{viewId}(§4.2 可分享)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { getApiClient } from '../../api/instance';
import { MeshApiError } from '../../api/errors';
import { Button, Dialog, EmptyState, ErrorState, Input, Select, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  createView,
  deleteView,
  duplicateView,
  listViews,
  setViewWip,
  updateView,
} from './api';
import { BoardColumns } from './BoardColumns';
import { columnsForView } from './columns';
import { FilterConfigPanel } from './FilterConfigPanel';
import { SortConfigPanel } from './SortConfigPanel';
import { ViewSaveBar } from './ViewSaveBar';
import { ViewSwitcher } from './ViewSwitcher';
import { WipConfigPanel } from './WipConfigPanel';
import type {
  BoardSettings,
  Filters,
  GroupByField,
  SortRule,
  View,
  WipEnforcement,
} from './types';
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

const GROUP_BY_OPTIONS: readonly (GroupByField)[] = [
  'state_category',
  'status',
  'assignee',
  'priority',
  'project',
  'label',
];

export function BoardPage(): React.JSX.Element {
  const t = useT();
  const navigate = useNavigate();
  const { viewId } = useParams<{ viewId: string }>();
  const toast = useToast();
  const client = useMemo(() => getApiClient(), []);

  // addToast 经 ref 持有:避免 toast 上下文对象每次渲染换引用而让 toastError/
  // load* 回调失效,进而触发挂载 effect 在加载失败路径上无限重跑(§6.12 错误态)。
  const addToastRef = useRef(toast.addToast);
  addToastRef.current = toast.addToast;

  const [membership, setMembership] = useState<Membership | null>(null);
  const [wsStatus, setWsStatus] = useState<LoadStatus>('loading');
  const [views, setViews] = useState<readonly View[]>([]);
  const [viewsStatus, setViewsStatus] = useState<LoadStatus>('loading');
  const [draft, setDraft] = useState<ViewDraft | null>(null);
  const [panel, setPanel] = useState<PanelKey | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [saveAsName, setSaveAsName] = useState('');

  const toastError = useCallback(
    (error: unknown) => {
      const message =
        error instanceof MeshApiError
          ? `${t('error.' + error.code)}`
          : t('common.unknownError');
      addToastRef.current(message, { tone: 'danger', closeLabel: t('common.close') });
    },
    [t],
  );

  const loadWorkspace = useCallback(async () => {
    setWsStatus('loading');
    try {
      const me = await fetchMe(client);
      const active = activeWorkspace(me.memberships);
      setMembership(active);
      setWsStatus(active === null ? 'empty' : 'ready');
    } catch (error) {
      setWsStatus('error');
      toastError(error);
    }
  }, [client, toastError]);

  const loadViews = useCallback(
    async (workspaceId: string) => {
      setViewsStatus('loading');
      try {
        const page = await listViews(client, workspaceId, { limit: 100 });
        setViews(page.data);
        setViewsStatus(page.data.length === 0 ? 'empty' : 'ready');
      } catch (error) {
        setViewsStatus('error');
        toastError(error);
      }
    },
    [client, toastError],
  );

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    if (membership !== null) {
      void loadViews(membership.workspace_id);
    }
  }, [membership, loadViews]);

  const selectedView = useMemo(() => {
    if (views.length === 0) return null;
    if (viewId !== undefined) {
      const match = views.find((view) => view.id === viewId);
      if (match !== undefined) return match;
    }
    return views.find((view) => view.is_default) ?? views[0] ?? null;
  }, [views, viewId]);

  // 选中视图变化时以其配置重置草稿(切换视图丢弃未保存改动前由 UI 提示;
  // 本切片切换即重置,保存条在脏态呈现 —— §4.2 保存/另存/丢弃)。
  useEffect(() => {
    setDraft(selectedView === null ? null : draftFromView(selectedView));
  }, [selectedView]);

  const selectView = useCallback(
    (nextId: string) => {
      navigate(`/board/${nextId}`);
    },
    [navigate],
  );

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
          onRetry={() => void loadWorkspace()}
        />
      </div>
    );
  }
  if (wsStatus === 'empty' || membership === null) {
    return (
      <div className="mesh-board" data-testid="board-page">
        <EmptyState title={t('board.noWorkspaceTitle')} description={t('board.noWorkspaceDescription')} />
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
        navigate('/board');
      }
    } catch (error) {
      toastError(error);
    }
  };

  if (viewsStatus === 'empty' || selectedView === null || draft === null) {
    return (
      <div className="mesh-board" data-testid="board-page">
        <ViewSwitcher
          views={views}
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
          title={t('board.emptyTitle')}
          description={t('board.emptyDescription')}
          action={
            <Button data-testid="board-empty-create" onClick={() => document.querySelector<HTMLButtonElement>('[data-testid="view-create-open"]')?.click()}>
              + {t('board.newView')}
            </Button>
          }
        />
      </div>
    );
  }

  const dirty = draftDiffers(selectedView, draft);
  const canWrite = selectedView.can_write === true;
  // 列派生以「草稿覆盖后的视图」计算,配置改动即时反映到列骨架。
  const previewView: View = {
    ...selectedView,
    group_by: draft.group_by,
    sub_group_by: draft.sub_group_by,
    filters: draft.filters,
    sort: draft.sort,
    board_settings: draft.board_settings,
  };
  const columns = columnsForView(previewView);

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
        // §6.14:409 → 拉最新收敛(服务端最新写为准)
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
      await setViewWip(client, selectedView.id, {
        group_key: groupKey,
        limit,
        enforcement,
      });
      await loadViews(workspaceId);
    } catch (error) {
      toastError(error);
    }
  };

  return (
    <div className="mesh-board" data-testid="board-page">
      <ViewSwitcher
        views={views}
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
          <span className="mesh-board__layout-chip">{t('board.layout.' + selectedView.layout)}</span>
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
                sub_group_by: event.target.value === '' ? null : (event.target.value as GroupByField),
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
          <SortConfigPanel
            rules={draft.sort}
            onChange={(sort) => setDraft({ ...draft, sort })}
          />
        ) : null}
        {panel === 'wip' ? <WipConfigPanel columns={columns} onSave={handleWipSave} /> : null}

        {selectedView.layout === 'board' ? (
          <BoardColumns
            columns={columns}
            groupBy={draft.group_by}
            onToggleCollapse={toggleCollapse}
          />
        ) : selectedView.layout === 'list' ? (
          <EmptyState
            title={t('board.listPlaceholderTitle')}
            description={t('board.listPlaceholderDescription')}
          />
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
            <Button onClick={() => void handleSaveAs()} disabled={saveAsName.trim() === '' || busy} data-testid="save-as-submit">
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
