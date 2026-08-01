/**
 * 单条审批卡(README §6.10 展示要求):subject 徽标(图标 + 文本)、动作摘要、
 * 所需权限(capability + permission)chip、影响范围、预估成本、过期时间
 * (相对 + 绝对 tooltip)、续跑提示(已完成 N 步 + 待执行工具)、subject 深链;
 * pending 行给 [批准]/[拒绝];过期给「已过期」+「重新发起」。数字 tabular-nums。
 */
import { Link } from 'react-router';
import { Badge, Button, StatusDot } from '../../design';
import { useT } from '../../i18n';
import type { Approval } from './api';
import {
  actionHeadline,
  estimatedCostOf,
  formatImpactScope,
  isExpiredApproval,
  pendingToolCallText,
  permissionChips,
  relativeParts,
  resumeCompletedSteps,
  subjectIcon,
  subjectLabelKey,
  subjectLink,
} from './summary';

export interface ApprovalCardProps {
  readonly approval: Approval;
  readonly nowMs: number;
  readonly onApprove?: (id: string) => void;
  readonly onReject?: (approval: Approval) => void;
  readonly isDeciding?: boolean;
}

/** 绝对时间(过期 tooltip / 决定时间;浏览器 locale,稳定可读)。 */
export function formatAbsolute(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function ExpiryLine(props: { readonly approval: Approval; readonly nowMs: number }): React.JSX.Element {
  const { approval, nowMs } = props;
  const t = useT();
  if (isExpiredApproval(approval, nowMs)) {
    return (
      <span className="mesh-approvals__meta-item" data-testid={`approval-expired-${approval.id}`}>
        <StatusDot tone="danger" label={t('approvals.expiredBadge')} />
      </span>
    );
  }
  if (approval.status !== 'pending') {
    return (
      <span className="mesh-approvals__meta-item">
        {t('approvals.expires')}{' '}
        <span className="mesh-tnum" title={formatAbsolute(approval.expires_at)}>
          {formatAbsolute(approval.expires_at)}
        </span>
      </span>
    );
  }
  const parts = relativeParts(approval.expires_at, nowMs);
  const relativeText = parts.past
    ? t('approvals.rel.past')
    : t(`approvals.rel.${parts.unit}`, { count: parts.value });
  return (
    <span className="mesh-approvals__meta-item">
      {t('approvals.expires')}{' '}
      <span
        className="mesh-tnum"
        data-testid={`approval-expires-${approval.id}`}
        title={formatAbsolute(approval.expires_at)}
      >
        {t('approvals.expiresIn', { time: relativeText })}
      </span>
    </span>
  );
}

function ResumeHint(props: { readonly approval: Approval }): React.JSX.Element | null {
  const { approval } = props;
  const t = useT();
  if (approval.subject_type === 'autopilot_action') return null;
  const steps = resumeCompletedSteps(approval.action_summary);
  if (steps === null) return null;
  const pendingTool = pendingToolCallText(approval.action_summary);
  return (
    <p className="mesh-approvals__resume" data-testid={`approval-resume-${approval.id}`}>
      {t('approvals.resumeHint', { steps })}
      {pendingTool !== null ? (
        <>
          <br />
          <span className="mesh-approvals__resume-tool">
            {t('approvals.pendingToolCall', { tool: pendingTool })}
          </span>
        </>
      ) : null}
    </p>
  );
}

export function ApprovalCard(props: ApprovalCardProps): React.JSX.Element {
  const { approval, nowMs, onApprove, onReject, isDeciding } = props;
  const t = useT();
  const summary = approval.action_summary;
  const chips = permissionChips(summary);
  const impact = formatImpactScope(summary.impact_scope);
  const cost = estimatedCostOf(summary);
  const headline = actionHeadline(summary);
  const link = subjectLink(approval);
  const expired = isExpiredApproval(approval, nowMs);
  const showDecideButtons = approval.status === 'pending' && !expired;

  return (
    <article className="mesh-approvals__card" data-testid={`approval-card-${approval.id}`}>
      <header className="mesh-approvals__card-head">
        <Badge tone="neutral" icon={subjectIcon(approval.subject_type)}>
          {t(subjectLabelKey(approval.subject_type))}
        </Badge>
        {headline !== null ? (
          <span className="mesh-approvals__action" data-testid={`approval-action-${approval.id}`}>
            {headline}
          </span>
        ) : null}
        {approval.status !== 'pending' ? (
          <span data-testid={`approval-status-${approval.id}`}>
            <Badge tone={approval.status === 'approved' ? 'success' : 'neutral'}>
              {t(`approvals.status.${approval.status}`)}
            </Badge>
          </span>
        ) : null}
      </header>

      {(chips.capability !== null || chips.permission !== null) && (
        <div className="mesh-approvals__chips">
          {chips.capability !== null ? (
            <Badge size="sm" tone="info" icon={null}>
              {`${t('approvals.chip.capability')}: ${chips.capability}`}
            </Badge>
          ) : null}
          {chips.permission !== null ? (
            <Badge size="sm" tone="info" icon={null}>
              {`${t('approvals.chip.permission')}: ${chips.permission}`}
            </Badge>
          ) : null}
        </div>
      )}

      <div className="mesh-approvals__meta">
        {impact !== null ? (
          <span className="mesh-approvals__meta-item" data-testid={`approval-impact-${approval.id}`}>
            {t('approvals.impact', { scope: impact })}
          </span>
        ) : null}
        {cost !== null ? (
          <span className="mesh-approvals__meta-item">
            <span className="mesh-tnum">{t('approvals.cost', { cost })}</span>
          </span>
        ) : null}
        <ExpiryLine approval={approval} nowMs={nowMs} />
        {link !== null ? (
          <Link
            className="mesh-page__link"
            to={link}
            data-testid={`approval-link-${approval.id}`}
          >
            {expired ? t('approvals.relaunch') : t('approvals.openSubject')}
          </Link>
        ) : null}
      </div>

      <ResumeHint approval={approval} />

      {approval.status !== 'pending' && approval.decision_comment !== null ? (
        <p className="mesh-approvals__comment">
          {t('approvals.decisionComment', { comment: approval.decision_comment })}
        </p>
      ) : null}

      {showDecideButtons ? (
        <div className="mesh-approvals__actions">
          <Button
            variant="primary"
            size="sm"
            isLoading={isDeciding === true}
            onClick={() => onApprove?.(approval.id)}
            data-testid={`approval-approve-${approval.id}`}
          >
            {t('approvals.approve')}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onReject?.(approval)}
            data-testid={`approval-reject-${approval.id}`}
          >
            {t('approvals.reject')}
          </Button>
        </div>
      ) : null}
    </article>
  );
}
