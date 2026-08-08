/**
 * Issue 列表批量操作条(design-quality.md §3.2:批量条粘底;§13.3:destructive 有确认)。
 * 设计层 BulkBar 承载计数/取消/动作槽;动作 = 状态菜单 + 优先级菜单(经 bulkIssues)
 * + 删除(确认 Dialog)。逐条结果「成功 N,失败 M」toast,部分失败附前若干条原因(issue.md §5.5)。
 */
import { useCallback, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { BulkBar, Button, Dialog, Menu, useToast } from '../../design';
import type { MenuItem } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import type { MemberSummary } from '../members/types';
import { bulkIssues } from './api';
import type { IssuePriority, IssueStatusRef } from './types';
import { PRIORITY_ORDER } from './types';

interface IssuesBulkBarProps {
  readonly selected: readonly string[];
  readonly statuses: readonly IssueStatusRef[];
  /** 工作区名册(批量转派候选;仅活跃成员可选)。 */
  readonly members: readonly MemberSummary[];
  readonly onDone: (summary: { succeeded: number; failed: number }) => void;
  readonly onClear: () => void;
}

interface BulkRunBody {
  readonly changes?: { priority?: IssuePriority; status_id?: string; assignee_id?: string };
  readonly delete?: boolean;
}

/** 部分失败 details 收窄(bulkIssues 422 bulk_partial_failure)。 */
function partialSummary(
  err: MeshApiError,
  fallbackFailed: number,
): { succeeded: number; failed: number; perItem: string } {
  const details = err.details as
    | {
        succeeded?: number;
        failed?: number;
        errors?: readonly { issue_id: string; code: string; message: string }[];
      }
    | undefined;
  const perItem = (details?.errors ?? [])
    .slice(0, 5)
    .map((e) => `${e.issue_id.slice(0, 8)}: ${e.code}`)
    .join('; ');
  return { succeeded: details?.succeeded ?? 0, failed: details?.failed ?? fallbackFailed, perItem };
}

export function IssuesBulkBar(props: IssuesBulkBarProps): React.JSX.Element | null {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [isBusy, setIsBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const run = useCallback(
    async (body: BulkRunBody) => {
      setIsBusy(true);
      try {
        const result = await bulkIssues(client, { issue_ids: props.selected, ...body });
        const summary = { succeeded: result.succeeded, failed: result.failed };
        props.onDone(summary);
        toast.addToast(t('issues.bulk.result', summary), {
          tone: summary.failed > 0 ? 'warn' : 'success',
          closeLabel: t('common.close'),
        });
      } catch (err: unknown) {
        if (err instanceof MeshApiError && err.code === 'bulk_partial_failure') {
          const { succeeded, failed, perItem } = partialSummary(err, props.selected.length);
          const summary = { succeeded, failed };
          props.onDone(summary);
          // F4:逐条失败原因可定位(§5.5)
          toast.addToast(`${t('issues.bulk.result', summary)}${perItem ? ` — ${perItem}` : ''}`, {
            tone: 'warn',
            closeLabel: t('common.close'),
          });
          return;
        }
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      } finally {
        setIsBusy(false);
      }
    },
    [client, props, t, toast],
  );

  const statusEntries: readonly MenuItem[] = props.statuses.map((s) => ({
    key: s.id,
    label: s.name,
    onSelect: () => void run({ changes: { status_id: s.id } }),
  }));

  const priorityEntries: readonly MenuItem[] = PRIORITY_ORDER.map((p) => ({
    key: p,
    label: t(`issues.priority.${p}`),
    onSelect: () => void run({ changes: { priority: p } }),
  }));

  // L247 批量转派:取消指派(空串 → 后端置 null)+ 活跃成员候选。
  const unassignEntry: MenuItem = {
    key: 'unassign',
    label: t('issues.bulk.unassign'),
    onSelect: () => void run({ changes: { assignee_id: '' } }),
  };
  const memberEntries: readonly MenuItem[] = props.members
    .filter((member) => member.status === 'active')
    .map((member) => ({
      key: member.id,
      label: member.display_name,
      onSelect: () => void run({ changes: { assignee_id: member.id } }),
    }));
  const assigneeEntries: readonly MenuItem[] = [unassignEntry, ...memberEntries];

  return (
    <>
      <BulkBar
        selectedCount={props.selected.length}
        countLabel={t('issues.bulk.selected', { count: props.selected.length })}
        onClearSelection={props.onClear}
        clearLabel={t('issues.bulk.clear')}
        ariaLabel={t('issues.bulk.ariaLabel')}
        actions={
          <>
            <Menu
              triggerLabel={t('issues.bulk.setStatus')}
              trigger={t('issues.bulk.setStatus')}
              entries={statusEntries}
            />
            <Menu
              triggerLabel={t('issues.bulk.setPriority')}
              trigger={t('issues.bulk.setPriority')}
              entries={priorityEntries}
            />
            <Menu
              triggerLabel={t('issues.bulk.setAssignee')}
              trigger={t('issues.bulk.setAssignee')}
              entries={assigneeEntries}
            />
            <Button
              variant="danger"
              size="sm"
              isLoading={isBusy}
              onClick={() => setConfirmDelete(true)}
            >
              {t('issues.bulk.delete')}
            </Button>
          </>
        }
      />
      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title={t('issues.bulk.deleteConfirmTitle')}
        closeLabel={t('common.close')}
      >
        <p className="mesh-text-body-sm" data-testid="bulk-delete-confirm-body">
          {t('issues.bulk.deleteConfirmBody', { count: props.selected.length })}
        </p>
        <div className="mesh-issues__confirm-actions">
          <Button
            variant="danger"
            isLoading={isBusy}
            data-testid="bulk-delete-confirm"
            onClick={() => {
              setConfirmDelete(false);
              void run({ delete: true });
            }}
          >
            {t('issues.bulk.delete')}
          </Button>
          <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
            {t('common.cancel')}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
