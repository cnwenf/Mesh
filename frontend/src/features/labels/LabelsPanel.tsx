/**
 * 标签管理面板(label-property.md §4.1/§4.2/§4.4 定义层):列表(色点 | 名称 | 作用域 | 操作)、
 * 新建 / 编辑(名称 + 颜色选择 + 描述)、删除二次确认。工作区设置与项目设置复用同一面板
 * (projectId 传入时创建项目级标签)。实时 label.* 帧触发列表刷新(§3.5 增量失效)。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { MeshApiError, errorToI18nKey } from '../../api';
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  Select,
  Skeleton,
  useToast,
} from '../../design';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import {
  createLabel,
  deleteLabel,
  listLabels,
  projectChannel,
  updateLabel,
  workspaceLabelsChannel,
} from './api';
import { ColorPicker, isValidHexColor } from './ColorPicker';
import { mergeLabel } from './associationApi';
import type { Label, LabelWithUsage } from './types';

interface LabelsPanelProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  /** 传入即项目设置上下文:新建为项目级标签,并订阅 project:{id} 频道。 */
  readonly projectId?: string;
}

interface LabelFormState {
  name: string;
  color: string;
  description: string;
}

const EMPTY_FORM: LabelFormState = {
  name: '',
  // mesh-data-color: 标签数据色板默认值(数据色非主题取色,theme.md §2.5 合法例外,on-color 经亮度阈值自动配对)
  color: '#3e63dd',
  description: '',
};

async function fetchAllLabels(
  client: MeshApiClient,
  workspaceId: string,
  projectId?: string,
): Promise<readonly LabelWithUsage[]> {
  const collected: LabelWithUsage[] = [];
  let cursor: string | null = null;
  do {
    const page = await listLabels(client, workspaceId, {
      project_id: projectId,
      limit: 200,
      cursor: cursor ?? undefined,
    });
    collected.push(...page.data);
    cursor = page.nextCursor;
  } while (cursor !== null);
  return collected;
}

/** Project-private targets may only receive a source from that same project. */
function isSafeMergeTarget(source: Label, target: Label): boolean {
  if (source.id === target.id) return false;
  if (target.project_id === null) return true;
  return source.project_id !== null && source.project_id === target.project_id;
}

export function LabelsPanel(props: LabelsPanelProps): React.JSX.Element {
  const { client, workspaceId, projectId } = props;
  const t = useT();
  const { addToast } = useToast();
  const realtime = useRealtimeContext();

  const [labels, setLabels] = useState<readonly LabelWithUsage[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  const [dialogMode, setDialogMode] = useState<'closed' | 'create' | 'edit'>('closed');
  const [editing, setEditing] = useState<Label | null>(null);
  const [form, setForm] = useState<LabelFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [deleting, setDeleting] = useState<Label | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [mergeSource, setMergeSource] = useState<LabelWithUsage | null>(null);
  const [mergeTargetId, setMergeTargetId] = useState('');
  const [mergeError, setMergeError] = useState<string | null>(null);
  const [isMerging, setIsMerging] = useState(false);

  const refresh = useCallback(() => setRefreshTick((tick) => tick + 1), []);

  // 载入列表(工作区/项目切换与实时失效共用 refreshTick)。
  useEffect(() => {
    let cancelled = false;
    setLoadError(false);
    fetchAllLabels(client, workspaceId, projectId)
      .then((items) => {
        if (!cancelled) setLabels(items);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, projectId, refreshTick]);

  // 实时增量失效(§3.5):label.* 帧 → 刷新列表缓存。
  useEffect(() => {
    if (realtime === null) return;
    const channels = [workspaceLabelsChannel(workspaceId)];
    if (projectId !== undefined) channels.push(projectChannel(projectId));
    for (const channel of channels) realtime.client.subscribe(channel);
    const offFrame = realtime.client.onFrame((frame) => {
      if (!channels.includes(frame.channel)) return;
      if (frame.event.startsWith('label.')) refresh();
    });
    return () => {
      offFrame();
      for (const channel of channels) realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId, projectId, refresh]);

  const openCreate = (): void => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setDialogMode('create');
  };

  const openEdit = (label: Label): void => {
    setEditing(label);
    setForm({ name: label.name, color: label.color, description: label.description ?? '' });
    setFormError(null);
    setDialogMode('edit');
  };

  const closeDialog = (): void => setDialogMode('closed');

  const handleSave = async (): Promise<void> => {
    const name = form.name.trim();
    if (name.length === 0 || name.length > 50) {
      setFormError(t('labels.errors.nameLength'));
      return;
    }
    if (!isValidHexColor(form.color)) {
      setFormError(t('labels.errors.colorFormat'));
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      if (dialogMode === 'create') {
        await createLabel(client, workspaceId, {
          name,
          color: form.color,
          description: form.description.trim() === '' ? null : form.description.trim(),
          project_id: projectId ?? null,
        });
        addToast(t('labels.createdToast'), { tone: 'success', closeLabel: t('common.close') });
      } else if (editing !== null) {
        await updateLabel(
          client,
          editing.id,
          {
            name,
            color: form.color,
            description: form.description.trim() === '' ? null : form.description.trim(),
          },
          editing.updated_at,
        );
        addToast(t('labels.updatedToast'), { tone: 'success', closeLabel: t('common.close') });
      }
      closeDialog();
      refresh();
    } catch (err) {
      setFormError(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (deleting === null) return;
    setIsDeleting(true);
    try {
      await deleteLabel(client, deleting.id);
      addToast(t('labels.deletedToast'), { tone: 'info', closeLabel: t('common.close') });
      setDeleting(null);
      refresh();
    } catch (err) {
      addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  const openMerge = (source: LabelWithUsage): void => {
    setMergeSource(source);
    setMergeTargetId('');
    setMergeError(null);
  };

  const closeMerge = (): void => {
    if (isMerging) return;
    setMergeSource(null);
    setMergeTargetId('');
    setMergeError(null);
  };

  const handleMerge = async (): Promise<void> => {
    if (mergeSource === null || mergeTargetId === '') return;
    setIsMerging(true);
    setMergeError(null);
    try {
      const result = await mergeLabel(client, mergeSource.id, mergeTargetId);
      addToast(
        t('labels.mergeSuccess', {
          count: result.merged_issue_count,
          target: result.target_label.name,
        }),
        { tone: 'success', closeLabel: t('common.close') },
      );
      setMergeSource(null);
      setMergeTargetId('');
      refresh();
    } catch (err) {
      setMergeError(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'));
    } finally {
      setIsMerging(false);
    }
  };

  const dialogTitle =
    dialogMode === 'create' ? t('labels.dialog.createTitle') : t('labels.dialog.editTitle');

  return (
    <section aria-label={t('labels.sectionTitle')} data-testid="labels-panel">
      <div className="mesh-labels__header">
        <h3>{t('labels.sectionTitle')}</h3>
        <Button size="sm" onClick={openCreate} data-testid="labels-create">
          {t('labels.createButton')}
        </Button>
      </div>

      {labels === null ? (
        loadError ? (
          <ErrorState
            title={t('state.errorTitle')}
            description={t('state.errorDescription')}
            onRetry={refresh}
            retryLabel={t('common.retry')}
          />
        ) : (
          <Skeleton loadingLabel={t('state.loading')} />
        )
      ) : labels.length === 0 ? (
        <EmptyState title={t('labels.emptyTitle')} description={t('labels.emptyDescription')} />
      ) : (
        <ul className="mesh-labels__list" data-testid="labels-list">
          {labels.map((label) => (
            <li key={label.id} className="mesh-labels__row" data-testid={'label-row-' + label.name}>
              <span
                className="mesh-labels__dot"
                style={{ backgroundColor: label.color }}
                aria-hidden="true"
              />
              <span className="mesh-labels__name">{label.name}</span>
              <span className="mesh-labels__hex">{label.color}</span>
              {label.description !== null && label.description !== '' ? (
                <span className="mesh-labels__desc">{label.description}</span>
              ) : null}
              <span className="mesh-labels__scope">
                {label.scope === 'workspace'
                  ? t('labels.scopeWorkspace')
                  : t('labels.scopeProject')}
              </span>
              <span className="mesh-labels__usage">
                {t('labels.issueCount', { count: label.issue_count ?? 0 })}
              </span>
              <span className="mesh-labels__actions">
                <IconButton
                  label={t('labels.editButton', { name: label.name })}
                  size="sm"
                  variant="ghost"
                  data-testid={'label-edit-' + label.name}
                  onClick={() => openEdit(label)}
                >
                  {t('labels.editGlyph')}
                </IconButton>
                <IconButton
                  label={t('labels.mergeButton', { name: label.name })}
                  size="sm"
                  variant="ghost"
                  data-testid={'label-merge-' + label.name}
                  onClick={() => openMerge(label)}
                >
                  {t('labels.mergeGlyph')}
                </IconButton>
                <IconButton
                  label={t('labels.deleteButton', { name: label.name })}
                  size="sm"
                  variant="danger"
                  data-testid={'label-delete-' + label.name}
                  onClick={() => setDeleting(label)}
                >
                  {t('labels.deleteGlyph')}
                </IconButton>
              </span>
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={dialogMode !== 'closed'}
        onClose={closeDialog}
        title={dialogTitle}
        closeLabel={t('common.close')}
      >
        <div className="mesh-labels__dialog-body">
          <Input
            label={t('labels.dialog.nameLabel')}
            value={form.name}
            maxLength={50}
            data-testid="label-name-input"
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
          <ColorPicker
            label={t('labels.dialog.colorLabel')}
            hexInputLabel={t('labels.dialog.hexLabel')}
            value={form.color}
            onChange={(color) => setForm({ ...form, color })}
          />
          <Input
            label={t('labels.dialog.descriptionLabel')}
            value={form.description}
            data-testid="label-description-input"
            onChange={(event) => setForm({ ...form, description: event.target.value })}
          />
          {formError !== null ? (
            <p role="alert" data-testid="label-form-error">
              {formError}
            </p>
          ) : null}
          <div className="mesh-labels__dialog-footer">
            <Button variant="secondary" onClick={closeDialog}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void handleSave()} isLoading={isSaving} data-testid="label-save">
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={mergeSource !== null}
        onClose={closeMerge}
        title={t('labels.mergeTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-labels__dialog-body">
          <p data-testid="label-merge-source">
            {t('labels.mergeSource', { name: mergeSource?.name ?? '' })}
          </p>
          <Select
            label={t('labels.mergeTarget')}
            value={mergeTargetId}
            data-testid="label-merge-target"
            onChange={(event) => setMergeTargetId(event.target.value)}
          >
            <option value="">{t('labels.mergeTargetPlaceholder')}</option>
            {(labels ?? [])
              .filter((label) => mergeSource !== null && isSafeMergeTarget(mergeSource, label))
              .map((label) => (
                <option key={label.id} value={label.id}>
                  {label.name}
                </option>
              ))}
          </Select>
          <p data-testid="label-merge-impact">
            {t('labels.mergeImpact', { count: mergeSource?.issue_count ?? 0 })}
          </p>
          {mergeError !== null ? (
            <p role="alert" data-testid="label-merge-error">
              {mergeError}
            </p>
          ) : null}
          <div className="mesh-labels__dialog-footer">
            <Button variant="secondary" onClick={closeMerge} disabled={isMerging}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => void handleMerge()}
              isLoading={isMerging}
              disabled={mergeTargetId === ''}
              data-testid="label-merge-confirm"
            >
              {t('labels.mergeConfirm')}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={t('labels.deleteTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-labels__dialog-body">
          <p data-testid="label-delete-confirm-text">
            {deleting !== null ? t('labels.deleteConfirm', { name: deleting.name }) : ''}
          </p>
          <div className="mesh-labels__dialog-footer">
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => void handleDelete()}
              isLoading={isDeleting}
              data-testid="label-delete-confirm"
            >
              {t('common.confirm')}
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}
