/**
 * 邀请接受页(/invite/:token,workspace.md §4.3/§4.4)。
 *
 * 流程:preview(公开,恒 200)→ 有效卡(工作区名/角色/过期时间)+ 接受;
 * 未登录 → 登录页(?next= 回跳);接受成功 → 成功态(重加入同为成功态,
 * Leader 裁决 pin@MES-14:重激活既有名册行,UI 不区分);422 invitation_invalid
 * 与 preview valid:false 的四 reason(not_found/expired/exhausted/revoked)各呈 UI 态,
 * 不泄漏工作区存在性(not_found 与不存在同形)。token 仅经路径传递,不落入 UI 文案/日志。
 */
import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { getApiClient } from '../../api/instance';
import { acceptInvitation, previewInvitation } from '../../api/invitations';
import type {
  AcceptInvitationResult,
  InvitationPreview,
  InvitationRejectReason,
} from '../../api/invitations';
import { getToken } from '../../api/tokenStore';
import { Button } from '../../design';
import { formatWithZoneAnnotation, useT } from '../../i18n';
import { useSettingsStore } from '../../state/settingsStore';

type AcceptPhase =
  | { kind: 'previewing' }
  | { kind: 'preview'; preview: InvitationPreview }
  | { kind: 'accepting' }
  | { kind: 'accepted'; result: AcceptInvitationResult }
  | { kind: 'rejected'; reason: InvitationRejectReason };

export interface InviteAcceptPageProps {
  client?: MeshApiClient;
}

export function InviteAcceptPage(props: InviteAcceptPageProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const { token = '' } = useParams();
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const preferences = useSettingsStore((state) => state.preferences);
  const [phase, setPhase] = useState<AcceptPhase>({ kind: 'previewing' });

  const loadPreview = useCallback(async (): Promise<void> => {
    setPhase({ kind: 'previewing' });
    try {
      const preview = await previewInvitation(client, token);
      setPhase({ kind: 'preview', preview });
    } catch {
      // preview 恒 200;网络/解析失败按 not_found 同形呈现(不泄漏内部细节)
      setPhase({ kind: 'rejected', reason: 'not_found' });
    }
  }, [client, token]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  const handleAccept = async (): Promise<void> => {
    if (getToken() === null) {
      navigate(`/login?next=${encodeURIComponent(location.pathname)}`);
      return;
    }
    setPhase({ kind: 'accepting' });
    try {
      const result = await acceptInvitation(client, token);
      setPhase({ kind: 'accepted', result });
    } catch (err) {
      if (err instanceof MeshApiError && err.code === 'invitation_invalid') {
        const reason = (err.details ?? {}).reason;
        setPhase({
          kind: 'rejected',
          reason:
            reason === 'expired' || reason === 'exhausted' || reason === 'revoked'
              ? reason
              : 'not_found',
        });
        return;
      }
      setPhase({ kind: 'rejected', reason: 'not_found' });
    }
  };

  if (phase.kind === 'previewing') {
    return (
      <div className="mesh-invite" data-testid="invite-loading" role="status">
        {t('common.loading')}
      </div>
    );
  }

  if (phase.kind === 'rejected') {
    return (
      <div className="mesh-invite" data-testid={`invite-reason-${phase.reason}`}>
        <h1>{t(`invite.reason.${phase.reason}.title`)}</h1>
        <p>{t(`invite.reason.${phase.reason}.description`)}</p>
        <Link to="/">{t('invite.backHome')}</Link>
      </div>
    );
  }

  if (phase.kind === 'accepted') {
    const { result } = phase;
    return (
      <div className="mesh-invite" data-testid="invite-accepted">
        <h1>{t('invite.acceptedTitle')}</h1>
        <p>{t('invite.acceptedDescription', { workspace: result.workspace.name })}</p>
        <Button data-testid="invite-enter" onClick={() => navigate(`/w/${result.workspace.slug}`)}>
          {t('invite.enterWorkspace', { workspace: result.workspace.name })}
        </Button>
      </div>
    );
  }

  if (phase.kind === 'accepting') {
    return (
      <div className="mesh-invite" data-testid="invite-accepting" role="status">
        {t('common.loading')}
      </div>
    );
  }

  const { preview } = phase;
  if (!preview.valid) {
    return (
      <div className="mesh-invite" data-testid={`invite-reason-${preview.reason}`}>
        <h1>{t(`invite.reason.${preview.reason}.title`)}</h1>
        <p>{t(`invite.reason.${preview.reason}.description`)}</p>
        <Link to="/">{t('invite.backHome')}</Link>
      </div>
    );
  }

  const loggedIn = getToken() !== null;
  return (
    <div className="mesh-invite" data-testid="invite-preview">
      <h1>{t('invite.previewTitle', { workspace: preview.workspace_name })}</h1>
      <p data-testid="invite-preview-role">
        {t('invite.previewRole', { role: t(`roles.${preview.role}`) })}
      </p>
      <p>
        {t('invite.previewExpires', {
          when: formatWithZoneAnnotation(preview.expires_at, {
            locale: preferences.locale ?? 'en',
            timeZone: preferences.timezone,
          }),
        })}
      </p>
      {loggedIn ? (
        <Button data-testid="invite-accept" onClick={() => void handleAccept()}>
          {t('invite.acceptButton')}
        </Button>
      ) : (
        <>
          <p>{t('invite.loginHint')}</p>
          <Button data-testid="invite-login" onClick={() => void handleAccept()}>
            {t('invite.loginButton')}
          </Button>
        </>
      )}
    </div>
  );
}
