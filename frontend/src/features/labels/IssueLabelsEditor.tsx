/**
 * issue 详情侧栏的标签选择器(label-property.md §4.2)。
 * 彩色 chip + 输入联想(名称子串)+ 就地新建(颜色选择)+ 增量合并:
 * issue.labels_changed 帧直接更新本地标签集,label.* 帧刷新联想列表。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { errorToI18nKey, MeshApiError } from '../../api';
import { Button, Dialog, Input, useToast } from '../../design';
import { useT } from '../../i18n';
import type { RealtimeContextValue } from '../../shell/AppShell';
import type { RealtimeEventFrame } from '../../types/realtime';
import type { Label } from './types';
import { createLabel, listLabels, projectChannel, workspaceLabelsChannel } from './api';
import { addIssueLabel, listIssueLabels, removeIssueLabel } from './associationApi';
import { ColorPicker, PRESET_COLORS } from './ColorPicker';
import './labels.css';

export interface IssueLabelsEditorProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly projectId: string | null;
  readonly issueId: string;
  readonly reloadKey: number;
  /** 页面重取后才变化的 issue 令牌;驱动编辑器重取(不用 reloadKey,
   * 因子组件 effect 先于页面 effect 执行,会抢占页面重取的响应顺序)。 */
  readonly issueUpdatedAt: string;
  readonly realtime: RealtimeContextValue | null;
  /**
   * Called after a successful label mutation: association writes advance the
   * issue's updated_at/version (§5.4), so the page must refresh its issue to
   * keep its If-Match arbitration token fresh.
   */
  readonly onIssueChanged?: () => void;
}

async function drainLabels(
  client: MeshApiClient,
  workspaceId: string,
  projectId: string | null,
): Promise<readonly Label[]> {
  const collected: Label[] = [];
  let cursor: string | null = null;
  do {
    const page = await listLabels(client, workspaceId, {
      project_id: projectId ?? undefined,
      limit: 200,
      cursor: cursor ?? undefined,
    });
    // 防御异常包络(非数组 data 视为空页),避免渲染期崩溃。
    collected.push(...(Array.isArray(page.data) ? page.data : []));
    // 仅字符串游标才继续翻页(缺失/undefined 视为末页,防御异常包络)。
    cursor = typeof page.nextCursor === 'string' ? page.nextCursor : null;
  } while (cursor !== null);
  return collected;
}

export function IssueLabelsEditor(props: IssueLabelsEditorProps): React.JSX.Element {
  const { client, workspaceId, projectId, issueId, issueUpdatedAt, realtime } = props;
  const t = useT();
  const toast = useToast();
  const [current, setCurrent] = useState<readonly Label[]>([]);
  const [available, setAvailable] = useState<readonly Label[]>([]);
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [newColor, setNewColor] = useState(PRESET_COLORS[0]);
  const busy = useRef(false);

  // addToast 经 ref 持有:toast 上下文对象每次渲染换引用,直接进依赖会让
  // 加载 effect 在持续报错路径上无限重跑(BoardPage v0.11.6 同源修复)。
  const addToastRef = useRef(toast.addToast);
  addToastRef.current = toast.addToast;

  const reportError = useCallback(
    (error: unknown) => {
      addToastRef.current(
        t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.network'),
        { tone: 'danger', closeLabel: t('common.close') },
      );
    },
    [t],
  );

  // (Re)load current labels + the suggestion catalog. Deferred one
  // On mount + whenever the issue's arbitration token (updated_at) changes,
  // i.e. AFTER the page's own reload has resolved. Not keyed on reloadKey:
  // child effects run before the parent's, so a reloadKey-driven refetch
  // would race the page's reload fetches (and the tests' positional stub).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [issueLabels, allLabels] = await Promise.all([
          listIssueLabels(client, issueId),
          drainLabels(client, workspaceId, projectId),
        ]);
        if (cancelled) return;
        setCurrent(Array.isArray(issueLabels) ? issueLabels : []);
        setAvailable(Array.isArray(allLabels) ? allLabels : []);
      } catch (error) {
        if (!cancelled) reportError(error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, issueId, workspaceId, projectId, issueUpdatedAt, reportError]);

  // Incremental merge (§3.5): labels_changed updates chips in place;
  // label.* frames refresh the suggestion catalog.
  useEffect(() => {
    if (realtime === null) return;
    realtime.client.subscribe(workspaceLabelsChannel(workspaceId));
    if (projectId !== null) realtime.client.subscribe(projectChannel(projectId));
    const off = realtime.client.onFrame((frame: RealtimeEventFrame) => {
      if (frame.op !== 'event') return;
      const event = frame.event;
      const payload = (frame as { payload?: Record<string, unknown> }).payload ?? {};
      if (event === 'issue.labels_changed' && payload.issue_id === issueId) {
        const labels = payload.labels;
        if (Array.isArray(labels)) setCurrent(labels as readonly Label[]);
        return;
      }
      if (event.startsWith('label.')) {
        void drainLabels(client, workspaceId, projectId)
          .then(setAvailable)
          .catch(() => undefined);
      }
    });
    return () => {
      off();
      realtime.client.unsubscribe(workspaceLabelsChannel(workspaceId));
      if (projectId !== null) realtime.client.unsubscribe(projectChannel(projectId));
    };
  }, [realtime, client, workspaceId, projectId, issueId]);

  const currentIds = useMemo(() => new Set(current.map((l) => l.id)), [current]);
  const suggestions = useMemo(() => {
    const q = query.trim().toLowerCase();
    return available.filter(
      (label) =>
        !currentIds.has(label.id) && (q === '' || label.name.toLowerCase().includes(q)),
    );
  }, [available, currentIds, query]);

  const onIssueChanged = props.onIssueChanged;

  const add = useCallback(
    async (labelId: string) => {
      if (busy.current) return;
      busy.current = true;
      try {
        const result = await addIssueLabel(client, issueId, labelId);
        setCurrent(result.labels);
        setQuery('');
        onIssueChanged?.();
      } catch (error) {
        reportError(error);
      } finally {
        busy.current = false;
      }
    },
    [client, issueId, reportError, onIssueChanged],
  );

  const remove = useCallback(
    async (labelId: string) => {
      if (busy.current) return;
      busy.current = true;
      try {
        const result = await removeIssueLabel(client, issueId, labelId);
        setCurrent(result.labels);
        onIssueChanged?.();
      } catch (error) {
        reportError(error);
      } finally {
        busy.current = false;
      }
    },
    [client, issueId, reportError, onIssueChanged],
  );

  const createAndAttach = useCallback(async () => {
    const name = query.trim();
    if (name === '') return;
    try {
      const created = await createLabel(client, workspaceId, {
        name,
        color: newColor,
        project_id: projectId,
      });
      setAvailable((prev) => [...prev, created]);
      setCreating(false);
      await add(created.id);
    } catch (error) {
      reportError(error);
    }
  }, [add, client, newColor, projectId, query, reportError, workspaceId]);

  return (
    <section className="mesh-issue-labels" aria-label={t('issueLabels.sectionTitle')}>
      <h4 className="mesh-issues-detail__sidebar-heading">{t('issueLabels.sectionTitle')}</h4>
      <ul className="mesh-issue-labels__chips" data-testid="issue-label-chips">
        {current.map((label) => (
          <li key={label.id} className="mesh-issue-labels__chip">
            <span
              className="mesh-labels__dot"
              style={{ backgroundColor: label.color }}
              aria-hidden="true"
            />
            <span className="mesh-issue-labels__chip-name">{label.name}</span>
            <button
              type="button"
              className="mesh-issue-labels__chip-remove"
              aria-label={t('issueLabels.remove', { name: label.name })}
              onClick={() => void remove(label.id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <Input
        label={t('issueLabels.addPlaceholder')}
        value={query}
        data-testid="issue-label-search"
        onChange={(event) => setQuery(event.target.value)}
      />
      {query.trim() !== '' && (
        <ul className="mesh-issue-labels__suggest" data-testid="issue-label-suggest">
          {suggestions.map((label) => (
            <li key={label.id}>
              <button
                type="button"
                className="mesh-issue-labels__suggest-item"
                onClick={() => void add(label.id)}
              >
                <span
                  className="mesh-labels__dot"
                  style={{ backgroundColor: label.color }}
                  aria-hidden="true"
                />
                {label.name}
              </button>
            </li>
          ))}
          {suggestions.length === 0 && (
            <li>
              <button
                type="button"
                className="mesh-issue-labels__suggest-item"
                data-testid="issue-label-create-inline"
                onClick={() => {
                  setNewColor(PRESET_COLORS[0]);
                  setCreating(true);
                }}
              >
                {t('issueLabels.createInline', { name: query.trim() })}
              </button>
            </li>
          )}
        </ul>
      )}
      <Dialog
        open={creating}
        title={t('issueLabels.createTitle', { name: query.trim() })}
        closeLabel={t('common.close')}
        onClose={() => setCreating(false)}
      >
          <div className="mesh-labels__dialog-body">
            <ColorPicker
              label={t('issueLabels.colorLabel')}
              value={newColor}
              onChange={setNewColor}
              hexInputLabel={t('issueLabels.colorHex')}
            />
          </div>
          <div className="mesh-labels__dialog-footer">
            <Button variant="secondary" onClick={() => setCreating(false)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void createAndAttach()}>{t('common.create')}</Button>
          </div>
        </Dialog>
    </section>
  );
}
