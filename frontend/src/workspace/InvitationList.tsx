/**
 * 邀请列表(workspace.md §4.2:邮箱/角色/状态/过期时间/撤销按钮)。
 *
 * 游标分页(load more);状态四态(active/revoked/expired/exhausted,§4.4)+ 用量 used/max;
 * 过期时间按用户时区本地化(i18n.md §1.1);撤销仅 active 可操作(非 active → 409 提示刷新);
 * realtime `invitation.redeemed` 帧合并 used_count(达 max_uses 即 exhausted 呈现,§4.5)。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:组件与同域纯函数/常量同文件共存 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../api/client';
import { MeshApiError, errorToI18nKey } from '../api/errors';
import { getApiClient } from '../api/instance';
import { listInvitations, revokeInvitation } from '../api/invitations';
import type { Invitation, InvitationStatus } from '../api/invitations';
import { Button, StatusDot, useToast } from '../design';
import type { StatusDotTone } from '../design';
import { formatWithZoneAnnotation, useT } from '../i18n';
import { useRealtimeContext } from '../shell/AppShell';
import { useSettingsStore } from '../state/settingsStore';
import { workspaceChannel } from './WorkspaceProvider';

const STATUS_TONE: Record<InvitationStatus, StatusDotTone> = {
  active: 'success',
  revoked: 'danger',
  expired: 'warn',
  exhausted: 'neutral',
};

/** 用量达上限即 exhausted 呈现(与后端惰性置位一致,§4.4) */
export function effectiveStatus(invitation: Invitation): InvitationStatus {
  if (invitation.status === 'active' && invitation.used_count >= invitation.max_uses) {
    return 'exhausted';
  }
  return invitation.status;
}

export interface InvitationListProps {
  workspaceId: string;
  /** 递增即触发重拉(创建邀请后) */
  refreshSignal?: number;
  client?: MeshApiClient;
}

export function InvitationList(props: InvitationListProps): React.JSX.Element {
  const { workspaceId, refreshSignal } = props;
  const client = props.client ?? getApiClient();
  const t = useT();
  const { addToast } = useToast();
  const preferences = useSettingsStore((state) => state.preferences);
  const realtime = useRealtimeContext();

  const [items, setItems] = useState<readonly Invitation[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const loadFirst = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const page = await listInvitations(client, workspaceId);
      setItems(page.data);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setLoadError(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
    } finally {
      setIsLoading(false);
    }
  }, [client, workspaceId]);

  useEffect(() => {
    void loadFirst();
  }, [loadFirst, refreshSignal]);

  const loadMore = async (): Promise<void> => {
    if (nextCursor === null) return;
    setIsFetchingMore(true);
    try {
      const page = await listInvitations(client, workspaceId, { cursor: nextCursor });
      setItems((prev) => [...prev, ...page.data]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('a11y.dismiss'),
      });
    } finally {
      setIsFetchingMore(false);
    }
  };

  // realtime:invitation.redeemed → 合并 used_count(管理员侧实时,§4.5)。
  useEffect(() => {
    if (realtime === null) return;
    const channel = workspaceChannel(workspaceId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel || frame.event !== 'invitation.redeemed') return;
      const payload = frame.payload as {
        invitation_id?: unknown;
        used_count?: unknown;
      };
      if (typeof payload.invitation_id !== 'string') return;
      const usedCount = typeof payload.used_count === 'number' ? payload.used_count : undefined;
      setItems((prev) =>
        prev.map((invitation) => {
          if (invitation.id !== payload.invitation_id) return invitation;
          const nextUsed = usedCount ?? invitation.used_count + 1;
          return { ...invitation, used_count: nextUsed };
        }),
      );
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId]);

  const revoke = async (invitation: Invitation): Promise<void> => {
    setRevokingId(invitation.id);
    try {
      const revoked = await revokeInvitation(client, workspaceId, invitation.id);
      setItems((prev) => prev.map((item) => (item.id === revoked.id ? revoked : item)));
      addToast(t('invitations.revokedToast'), {
        tone: 'success',
        closeLabel: t('a11y.dismiss'),
      });
    } catch (err) {
      if (err instanceof MeshApiError && err.code === 'conflict') {
        // 状态已被他人/惰性流转(如过期)→ 重拉对齐
        await loadFirst();
      }
      addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('a11y.dismiss'),
      });
    } finally {
      setRevokingId(null);
    }
  };

  if (isLoading) {
    return (
      <p role="status" data-testid="invitation-list-loading">
        {t('common.loading')}
      </p>
    );
  }
  if (loadError !== null) {
    return (
      <div data-testid="invitation-list-error">
        <p role="alert">{t(loadError)}</p>
        <Button variant="secondary" onClick={() => void loadFirst()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }
  if (items.length === 0) {
    return <p data-testid="invitation-list-empty">{t('invitations.listEmpty')}</p>;
  }

  return (
    <div className="mesh-invite-list" data-testid="invitation-list">
      <table>
        <caption className="sr-only">{t('invitations.sectionTitle')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('invitations.colTarget')}</th>
            <th scope="col">{t('invitations.colRole')}</th>
            <th scope="col">{t('invitations.colStatus')}</th>
            <th scope="col">{t('invitations.colUses')}</th>
            <th scope="col">{t('invitations.colExpires')}</th>
            <th scope="col">{t('invitations.colAction')}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((invitation) => {
            const status = effectiveStatus(invitation);
            return (
              <tr key={invitation.id} data-testid="invitation-row">
                <td>
                  {invitation.email ??
                    t('invitations.linkTarget', { prefix: invitation.token_prefix })}
                </td>
                <td>{t(`roles.${invitation.role}`)}</td>
                <td>
                  <StatusDot tone={STATUS_TONE[status]} label={t(`invitations.status.${status}`)} />
                </td>
                <td data-testid="invitation-uses">
                  {invitation.used_count}/{invitation.max_uses}
                </td>
                <td>
                  {formatWithZoneAnnotation(invitation.expires_at, {
                    locale: preferences.locale ?? 'en',
                    timeZone: preferences.timezone,
                  })}
                </td>
                <td>
                  {status === 'active' ? (
                    <Button
                      size="sm"
                      variant="danger"
                      data-testid="invitation-revoke"
                      isLoading={revokingId === invitation.id}
                      onClick={() => void revoke(invitation)}
                    >
                      {t('invitations.revoke')}
                    </Button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {nextCursor !== null ? (
        <Button
          variant="secondary"
          data-testid="invitation-load-more"
          isLoading={isFetchingMore}
          onClick={() => void loadMore()}
        >
          {t('invitations.loadMore')}
        </Button>
      ) : null}
    </div>
  );
}
