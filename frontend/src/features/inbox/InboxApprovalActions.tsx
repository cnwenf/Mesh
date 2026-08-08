/**
 * 收件箱行内联审批入口(agent.md §5.4 / README §6.10,L206)。
 *
 * review_requested 通知携带 approval_id(B1 fanout):本组件据 approval_id 拉取
 * 审批当前态,仅 pending(且未过 reaper 惰性窗口)渲染内联批准/拒绝,复用
 * `POST /approvals/{id}/approve|reject`(服务端幂等)。决定后行内态收敛为
 * 状态徽标;已决/过期/取消不再出现按钮,防 stale 操作。其他会话的决定经
 * `workspace:{ws}:executions` 频道 `approval.decided` 帧同步收敛。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Badge, Button, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { approveApproval, getApproval, rejectApproval } from '../approvals/api';
import type { Approval, ApprovalStatus } from '../approvals/api';
import { isExpiredApproval } from '../approvals/summary';
import type { RealtimeEventFrame } from '../../types/realtime';

const DECIDED_STATUSES: readonly ApprovalStatus[] = [
  'approved',
  'rejected',
  'expired',
  'cancelled',
];

function decidedStatusOf(frame: RealtimeEventFrame): ApprovalStatus | null {
  if (frame.event !== 'approval.decided') return null;
  const payload = frame.payload as { decision?: unknown } | null;
  const decision = payload?.decision;
  return typeof decision === 'string' && (DECIDED_STATUSES as readonly string[]).includes(decision)
    ? (decision as ApprovalStatus)
    : null;
}

function toastKeyForError(err: unknown): string {
  return err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown';
}

export interface InboxApprovalActionsProps {
  readonly workspaceId: string;
  readonly approvalId: string;
}

export function InboxApprovalActions(props: InboxApprovalActionsProps): React.JSX.Element | null {
  const { workspaceId, approvalId } = props;
  const t = useT();
  const { addToast } = useToast();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [decidedStatus, setDecidedStatus] = useState<ApprovalStatus | null>(null);
  const [isDeciding, setIsDeciding] = useState(false);

  // 行挂载即拉取审批当前态:仅 pending 才给按钮(已决/过期/取消 → 不渲染按钮)。
  useEffect(() => {
    let cancelled = false;
    void getApproval(client, workspaceId, approvalId)
      .then((row) => {
        if (!cancelled) setApproval(row);
      })
      .catch(() => {
        // 查不到(stale 通知 / 权限不足):静默不渲染,行本身仍可正常浏览。
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, approvalId]);

  // 多端同步:其他会话决定后,行内按钮收敛为状态徽标。
  useEffect(() => {
    if (realtime === null) return;
    const channel = `workspace:${workspaceId}:executions`;
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      const payload = frame.payload as { approval_id?: unknown } | null;
      if (payload?.approval_id !== approvalId) return;
      const status = decidedStatusOf(frame);
      if (status !== null) setDecidedStatus(status);
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId, approvalId]);

  const decide = useCallback(
    async (approve: boolean) => {
      setIsDeciding(true);
      try {
        const committed = approve
          ? await approveApproval(client, workspaceId, approvalId, {})
          : await rejectApproval(client, workspaceId, approvalId, {});
        setApproval(committed);
        // 并发决定时服务端幂等返回当前态(可能非本次请求的决定)——以服务端为准。
        if (committed.status !== 'pending') setDecidedStatus(committed.status);
        const toastKey =
          committed.status === 'approved' || committed.status === 'rejected'
            ? `approvals.toast.${committed.status}`
            : `approvals.status.${committed.status}`;
        addToast(t(toastKey), {
          tone:
            committed.status === 'approved' || committed.status === 'rejected' ? 'success' : 'info',
          closeLabel: t('a11y.dismiss'),
        });
      } catch (err: unknown) {
        addToast(t(toastKeyForError(err)), { tone: 'danger', closeLabel: t('a11y.dismiss') });
      } finally {
        setIsDeciding(false);
      }
    },
    [client, workspaceId, approvalId, addToast, t],
  );

  const effectiveStatus: ApprovalStatus | null =
    decidedStatus ?? (approval !== null ? approval.status : null);
  if (effectiveStatus !== null && effectiveStatus !== 'pending') {
    return (
      <span
        className="mesh-inbox__row-approval"
        data-testid={`inbox-approval-decided-${approvalId}`}
      >
        <Badge tone={effectiveStatus === 'approved' ? 'success' : 'neutral'} size="sm">
          {t(`approvals.status.${effectiveStatus}`)}
        </Badge>
      </span>
    );
  }
  if (approval === null || approval.status !== 'pending') return null;
  if (isExpiredApproval(approval, Date.now())) return null;

  return (
    <span className="mesh-inbox__row-approval">
      <Button
        variant="primary"
        size="sm"
        isLoading={isDeciding}
        onClick={() => void decide(true)}
        data-testid={`inbox-approval-approve-${approvalId}`}
      >
        {t('approvals.approve')}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        disabled={isDeciding}
        onClick={() => void decide(false)}
        data-testid={`inbox-approval-reject-${approvalId}`}
      >
        {t('approvals.reject')}
      </Button>
    </span>
  );
}
