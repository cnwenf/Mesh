/**
 * Issue 属性侧栏(issue.md §4.2:每字段点击即编辑;桌面 320px 侧栏 / 手机经
 * DetailLayout 的 Drawer 底部 sheet 呈现同一内容,§8.3)。
 * 状态选择器按 category 分组(§1.2.3);项目为两步式迁移入口(§4.3/§3.8)。
 * 本组件不持有乐观逻辑:经 onPatch 上抛,由页面统一 mutation + 冲突收敛。
 */
import { Input, Select } from '../../design';
import type { MeshApiClient } from '../../api';
import { useT } from '../../i18n';
import type { RealtimeContextValue } from '../../shell/AppShell';
import type { MemberSummary } from '../members/types';
import type { Cycle, Milestone, ProjectSummary } from '../projects/types';
import { IssueCustomFieldsEditor } from '../labels/IssueCustomFieldsEditor';
import { IssueLabelsEditor } from '../labels/IssueLabelsEditor';
import { VcsLinksPanel } from '../integrations/VcsLinksPanel';
import { IssueSquadAssignment } from './IssueSquadAssignment';
import type { IssueDetail, IssuePriority, IssueStatusRef } from './types';
import { PRIORITY_ORDER, STATE_CATEGORY_ORDER } from './types';
import './issues.css';

export interface IssuePropertiesProps {
  readonly workspaceSlug: string;
  readonly issue: IssueDetail;
  readonly statuses: readonly IssueStatusRef[];
  readonly members: readonly MemberSummary[];
  readonly projects: readonly ProjectSummary[];
  readonly milestones: readonly Milestone[];
  readonly cycles: readonly Cycle[];
  readonly client: MeshApiClient;
  readonly realtime: RealtimeContextValue | null;
  readonly reloadKey: number;
  readonly statusStrictMode: boolean;
  readonly statusValidationError: string | null;
  readonly onPatch: (changes: Partial<IssueDetail>) => void;
  readonly onRequestMove: (targetProjectId: string | null) => void;
  readonly onIssueChanged: () => void;
}

export function IssueProperties(props: IssuePropertiesProps): React.JSX.Element {
  const t = useT();
  const {
    workspaceSlug,
    issue,
    statuses,
    members,
    projects,
    milestones,
    cycles,
    client,
    realtime,
    reloadKey,
    statusStrictMode,
    statusValidationError,
    onPatch,
    onRequestMove,
    onIssueChanged,
  } = props;
  const allowedTransitions = new Set(issue.status?.allowed_transitions ?? []);
  const transitionIsDisabled = (targetId: string): boolean =>
    statusStrictMode && targetId !== issue.status_id && !allowedTransitions.has(targetId);
  const showAgentAssigneeHint =
    members.find((member) => member.id === issue.assignee_id)?.member_type === 'agent';

  const groupedStatuses = STATE_CATEGORY_ORDER.map((category) => ({
    category,
    items: statuses.filter((s) => s.category === category),
  })).filter((group) => group.items.length > 0);

  return (
    <div className="mesh-issues-detail__properties">
      <div className="mesh-issues-detail__status-control">
        <Select
          label={t('issues.columns.status')}
          value={issue.status_id ?? ''}
          data-testid="issue-detail-status"
          aria-describedby={
            statusValidationError !== null
              ? 'issue-status-validation-error'
              : statusStrictMode
                ? 'issue-status-strict-hint'
                : undefined
          }
          onChange={(event) => {
            const statusId = event.target.value;
            if (statusId === issue.status_id || transitionIsDisabled(statusId)) return;
            onPatch({ status_id: statusId, version: issue.version });
          }}
        >
          {groupedStatuses.map((group) => (
            <optgroup key={group.category} label={t(`issues.category.${group.category}`)}>
              {group.items.map((s) => {
                const disabled = transitionIsDisabled(s.id);
                return (
                  <option
                    key={s.id}
                    value={s.id}
                    disabled={disabled}
                    title={disabled ? t('issues.strictTransitionUnavailable') : undefined}
                  >
                    {s.name}
                  </option>
                );
              })}
            </optgroup>
          ))}
        </Select>
        {statusStrictMode ? (
          <p
            id="issue-status-strict-hint"
            className="mesh-issues-detail__field-hint"
            data-testid="issue-status-strict-hint"
          >
            {t('issues.strictModeHint')}
          </p>
        ) : null}
        {statusValidationError !== null ? (
          <p
            id="issue-status-validation-error"
            className="mesh-issues-detail__field-error"
            role="alert"
            data-testid="issue-status-validation-error"
          >
            {statusValidationError}
          </p>
        ) : null}
      </div>
      <Select
        label={t('issues.priority.label')}
        value={issue.priority}
        data-testid="issue-detail-priority"
        onChange={(event) =>
          onPatch({
            priority: event.target.value as IssuePriority,
            version: issue.version,
          })
        }
      >
        {PRIORITY_ORDER.map((p) => (
          <option key={p} value={p}>
            {t(`issues.priority.${p}`)}
          </option>
        ))}
      </Select>
      <Select
        label={t('issues.columns.assignee')}
        value={issue.assignee_id ?? ''}
        data-testid="issue-detail-assignee"
        onChange={(event) => {
          const value = event.target.value;
          const assigneeId = value === '' ? null : value;
          if (assigneeId === issue.assignee_id) return;
          onPatch({ assignee_id: assigneeId, version: issue.version });
        }}
      >
        <option value="">{t('issues.unassigned')}</option>
        {members
          .filter((m) => m.status === 'active')
          .map((m) => (
            <option key={m.id} value={m.id}>
              {m.display_name}
              {m.member_type === 'agent' ? ` (${t('issues.agentBadge')})` : ''}
            </option>
          ))}
      </Select>
      {showAgentAssigneeHint ? (
        <p className="mesh-issues-detail__field-hint" data-testid="issue-agent-assignee-hint">
          {t('issues.agentAssigneeHint')}
        </p>
      ) : null}
      {/* 小队分派(§4.3-2):单一责任主体徽章 + 分派给小队入口(独立于人类负责人下拉)。 */}
      <IssueSquadAssignment
        workspaceId={issue.workspace_id}
        workspaceSlug={workspaceSlug}
        issueId={issue.id}
        onChanged={onIssueChanged}
      />
      <Input
        label={t('issues.detail.estimate')}
        type="number"
        min="0"
        step="0.5"
        value={issue.estimate ?? ''}
        onChange={(event) =>
          onPatch({
            estimate: event.target.value === '' ? null : Number(event.target.value),
            version: issue.version,
          })
        }
        data-testid="issue-detail-estimate"
      />
      <Select
        label={t('issues.detail.estimateUnit')}
        value={issue.estimate_unit ?? ''}
        data-testid="issue-detail-estimate-unit"
        onChange={(event) =>
          onPatch({
            estimate_unit: event.target.value === '' ? null : event.target.value,
            version: issue.version,
          } as Partial<IssueDetail>)
        }
      >
        <option value="">{t('issues.detail.noneOption')}</option>
        <option value="points">{t('issues.detail.estimateUnit.points')}</option>
        <option value="hours">{t('issues.detail.estimateUnit.hours')}</option>
      </Select>
      <Input
        label={t('issues.detail.start')}
        type="date"
        value={issue.start_date ?? ''}
        onChange={(event) =>
          onPatch({
            start_date: event.target.value === '' ? null : event.target.value,
            version: issue.version,
          })
        }
        data-testid="issue-detail-start"
      />
      <Input
        label={t('issues.columns.due')}
        type="date"
        value={issue.due_date ?? ''}
        onChange={(event) =>
          onPatch({
            due_date: event.target.value === '' ? null : event.target.value,
            version: issue.version,
          })
        }
        data-testid="issue-detail-due"
      />
      <Select
        label={t('issues.detail.milestone')}
        value={issue.milestone_id ?? ''}
        data-testid="issue-detail-milestone"
        onChange={(event) =>
          onPatch({
            milestone_id: event.target.value === '' ? null : event.target.value,
            version: issue.version,
          } as Partial<IssueDetail>)
        }
      >
        <option value="">{t('issues.detail.noneOption')}</option>
        {milestones.map((m) => (
          <option key={m.id} value={m.id}>
            {m.title}
          </option>
        ))}
      </Select>
      <Select
        label={t('issues.detail.cycle')}
        value={issue.cycle_id ?? ''}
        data-testid="issue-detail-cycle"
        onChange={(event) =>
          onPatch({
            cycle_id: event.target.value === '' ? null : event.target.value,
            version: issue.version,
          } as Partial<IssueDetail>)
        }
      >
        <option value="">{t('issues.detail.noneOption')}</option>
        {cycles.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </Select>
      <Select
        label={t('issues.detail.project')}
        value={issue.project_id ?? ''}
        data-testid="issue-detail-project"
        onChange={(event) => onRequestMove(event.target.value === '' ? null : event.target.value)}
      >
        <option value="">{t('issues.detail.inbox')}</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}（{p.key}）
          </option>
        ))}
      </Select>
      {/* label-property.md §4.2/§4.3 关联层:标签 picker + 自定义字段面板 */}
      <IssueLabelsEditor
        client={client}
        workspaceId={issue.workspace_id}
        projectId={issue.project_id}
        issueId={issue.id}
        reloadKey={reloadKey}
        issueUpdatedAt={issue.updated_at}
        realtime={realtime}
        onIssueChanged={onIssueChanged}
      />
      <IssueCustomFieldsEditor
        client={client}
        workspaceId={issue.workspace_id}
        issueId={issue.id}
        issueUpdatedAt={issue.updated_at}
        members={members}
        reloadKey={reloadKey}
        realtime={realtime}
        onIssueChanged={onIssueChanged}
      />
      {/* integrations.md §4.2:issue 侧栏 VCS 关联区块(关联 PR/commit/branch + 状态)。 */}
      <VcsLinksPanel workspaceId={issue.workspace_id} issueId={issue.id} />
      <p className="mesh-issues-detail__meta">
        {t('issues.detail.reporter')}:{' '}
        {issue.reporter !== null ? issue.reporter.name : t('issues.unassigned')}
      </p>
    </div>
  );
}
