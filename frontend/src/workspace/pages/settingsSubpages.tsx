/**
 * 工作区设置薄子页(search-command-palette.md §3.4 / README §6.12 信息架构):
 * 成员角色 / 审批策略 / 状态与字段 / 危险操作。均为 admin+ 门控(呈现级;
 * 权威校验在后端),无权呈现 §6.12 permission denied 异常态;内容复用既有
 * 组件/端点,不新增业务逻辑。
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import type { ReactNode } from 'react';
import { getApiClient } from '../../api/instance';
import { Skeleton } from '../../design';
import { listApprovals } from '../../features/approvals/api';
import type { Approval } from '../../features/approvals/api';
import { actionHeadline } from '../../features/approvals/summary';
import { listStatuses } from '../../features/issues/api';
import type { IssueStatusRef } from '../../features/issues/types';
import { useT } from '../../i18n';
import { DangerZone } from '../DangerZone';
import { RolesMatrix } from '../RolesMatrix';
import { useWorkspace, WorkspaceGate } from '../WorkspaceProvider';

/** admin+ 门控壳:加载/异常态经 WorkspaceGate;非 admin 呈现 permission denied。 */
function AdminSettingsGate(props: {
  readonly testId: string;
  readonly title: string;
  readonly children: (workspaceId: string, workspaceSlug: string, isOwner: boolean) => ReactNode;
}): React.JSX.Element {
  return (
    <WorkspaceGate>
      <AdminSettingsInner testId={props.testId} title={props.title}>
        {props.children}
      </AdminSettingsInner>
    </WorkspaceGate>
  );
}

function AdminSettingsInner(props: {
  readonly testId: string;
  readonly title: string;
  readonly children: (workspaceId: string, workspaceSlug: string, isOwner: boolean) => ReactNode;
}): React.JSX.Element | null {
  const { workspace, isAdmin, isOwner } = useWorkspace();
  const t = useT();
  if (workspace === null) return null;
  if (!isAdmin) {
    return (
      <div className="mesh-ws-settings" data-testid={`${props.testId}-denied`}>
        <h2>{t('state.permissionTitle')}</h2>
        <p>{t('state.permissionDescription')}</p>
        <p>{t('state.permissionHint')}</p>
      </div>
    );
  }
  return (
    <div className="mesh-ws-settings" data-testid={props.testId}>
      <h1>{props.title}</h1>
      {props.children(workspace.id, workspace.slug, isOwner)}
    </div>
  );
}

/** settings/members — 成员角色矩阵(roles 真源呈现)。 */
export function WorkspaceMembersSettingsPage(): React.JSX.Element {
  const t = useT();
  return (
    <AdminSettingsGate
      testId="ws-settings-members"
      title={t('shortcuts.actionOpenSettingsMembers')}
    >
      {(workspaceId) => (
        <section aria-label={t('roles.sectionTitle')}>
          <h2>{t('roles.sectionTitle')}</h2>
          <RolesMatrix workspaceId={workspaceId} />
        </section>
      )}
    </AdminSettingsGate>
  );
}

/** settings/approvals — 审批策略入口:当前待审批清单(统一「待我审批」同端点)。 */
export function WorkspaceApprovalsSettingsPage(): React.JSX.Element {
  const t = useT();
  return (
    <AdminSettingsGate
      testId="ws-settings-approvals"
      title={t('shortcuts.actionOpenSettingsApprovals')}
    >
      {(workspaceId, workspaceSlug) => (
        <ApprovalsPolicySection workspaceId={workspaceId} workspaceSlug={workspaceSlug} />
      )}
    </AdminSettingsGate>
  );
}

function ApprovalsPolicySection(props: {
  readonly workspaceId: string;
  readonly workspaceSlug: string;
}): React.JSX.Element {
  const t = useT();
  const [approvals, setApprovals] = useState<readonly Approval[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    void listApprovals(getApiClient(), props.workspaceId, { status: 'pending' })
      .then(({ data }) => {
        if (!cancelled) setApprovals(data);
      })
      .catch(() => {
        if (!cancelled) setApprovals([]);
      });
    return () => {
      cancelled = true;
    };
  }, [props.workspaceId]);

  if (approvals === null) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }
  return (
    <section aria-label={t('nav.approvals')}>
      <h2>{t('nav.approvals')}</h2>
      <p>
        <Link to={`/w/${props.workspaceSlug}/approvals`} data-testid="ws-approvals-inbox-link">
          {t('shortcuts.actionOpenApprovals')}
        </Link>
      </p>
      {approvals.length === 0 ? (
        <p data-testid="ws-approvals-empty">
          {t('state.emptyTitle')} · {t('state.emptyDescription')}
        </p>
      ) : (
        <ul data-testid="ws-approvals-policy-list">
          {approvals.map((approval) => (
            <li key={approval.id}>{approvalHeadline(approval)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function approvalHeadline(approval: Approval): string {
  const summary: unknown = approval.action_summary;
  if (typeof summary === 'string' && summary.trim() !== '') return summary;
  return actionHeadline(approval.action_summary) ?? approval.subject_type;
}

/** settings/fields — 状态与字段:工作区状态清单 + 标签/自定义字段子页入口。 */
export function WorkspaceFieldsSettingsPage(): React.JSX.Element {
  const t = useT();
  return (
    <AdminSettingsGate testId="ws-settings-fields" title={t('shortcuts.actionOpenSettingsFields')}>
      {(workspaceId, workspaceSlug) => (
        <FieldsSection workspaceId={workspaceId} workspaceSlug={workspaceSlug} />
      )}
    </AdminSettingsGate>
  );
}

function FieldsSection(props: {
  readonly workspaceId: string;
  readonly workspaceSlug: string;
}): React.JSX.Element {
  const t = useT();
  const [statuses, setStatuses] = useState<readonly IssueStatusRef[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    void listStatuses(getApiClient(), props.workspaceId)
      .then((rows) => {
        if (!cancelled) setStatuses(rows);
      })
      .catch(() => {
        if (!cancelled) setStatuses([]);
      });
    return () => {
      cancelled = true;
    };
  }, [props.workspaceId]);

  return (
    <>
      <section aria-label={t('shortcuts.actionOpenSettingsFields')}>
        <h2>{t('shortcuts.issueStatus')}</h2>
        {statuses === null ? (
          <Skeleton loadingLabel={t('common.loading')} />
        ) : (
          <ul data-testid="ws-fields-status-list">
            {statuses.map((status) => (
              <li key={status.id} data-testid={`ws-field-status-${status.id}`}>
                {status.name}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label={t('labels.sectionTitle')}>
        <h2>{t('labels.sectionTitle')}</h2>
        <p>
          <Link
            className="mesh-ws-settings__resource-link"
            to={`/w/${props.workspaceSlug}/settings/labels`}
            data-testid="ws-fields-labels-link"
          >
            {t('labels.pageTitle')}
          </Link>
        </p>
        <p>
          <Link
            className="mesh-ws-settings__resource-link"
            to={`/w/${props.workspaceSlug}/settings/custom-fields`}
            data-testid="ws-fields-custom-link"
          >
            {t('fields.pageTitle')}
          </Link>
        </p>
      </section>
    </>
  );
}

/** settings/danger — 危险操作区(owner 写;admin 可见门控呈现)。 */
export function WorkspaceDangerSettingsPage(): React.JSX.Element {
  const t = useT();
  return (
    <AdminSettingsGate testId="ws-settings-danger" title={t('shortcuts.actionOpenSettingsDanger')}>
      {(workspaceId, workspaceSlug, isOwner) =>
        isOwner ? (
          <section aria-label={t('danger.sectionTitle')}>
            <DangerZone workspaceId={workspaceId} workspaceSlug={workspaceSlug} />
          </section>
        ) : (
          <p data-testid="ws-settings-danger-owner-only">{t('state.permissionDescription')}</p>
        )
      }
    </AdminSettingsGate>
  );
}
