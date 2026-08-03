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
import type { ReactNode } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
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
import { Button, PublicFlowShell } from '../../design';
import {
  beginWorkspaceLoad as beginWorkspaceThemeLoad,
  endWorkspaceContext as endWorkspaceThemeContext,
  setWorkspaceDefaultFromPreview,
} from '../../state/workspaceThemeBridge';
import { formatWithZoneAnnotation, useT } from '../../i18n';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
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

  useDocumentTitle(t('title.invite'));

  const loadPreview = useCallback(async (): Promise<void> => {
    setPhase({ kind: 'previewing' });
    // theme.md §2.2:邀请接受页(未登录)协商链第 2 级经公开 invitation preview
    // 的 appearance.default_theme 解析。加载期间标记「期望本级解析但未就绪」,
    // 无显式账号偏好时呈现中性 skeleton 而非猜测主题(§2.3 ③)。
    beginWorkspaceThemeLoad();
    try {
      const preview = await previewInvitation(client, token);
      if (preview.valid) {
        setWorkspaceDefaultFromPreview(preview.appearance?.default_theme);
      } else {
        endWorkspaceThemeContext();
      }
      setPhase({ kind: 'preview', preview });
    } catch {
      // preview 恒 200;网络/解析失败按 not_found 同形呈现(不泄漏内部细节)
      endWorkspaceThemeContext();
      setPhase({ kind: 'rejected', reason: 'not_found' });
    }
  }, [client, token]);

  useEffect(() => {
    void loadPreview();
    return () => {
      // 离开邀请入口 → 回到「无工作区上下文」,协商链落系统级。
      endWorkspaceThemeContext();
    };
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

  const renderShell = (title: string, content: ReactNode): React.JSX.Element => (
    <PublicFlowShell
      brandLabel={t('brand.name')}
      brandHref="/"
      title={title}
      skipLabel={t('a11y.skipLink')}
    >
      {content}
    </PublicFlowShell>
  );

  if (phase.kind === 'previewing') {
    return renderShell(
      t('title.invite'),
      <div className="mesh-invite-content" data-testid="invite-loading" role="status">
        {t('common.loading')}
      </div>,
    );
  }

  if (phase.kind === 'rejected') {
    return renderShell(
      t(`invite.reason.${phase.reason}.title`),
      <div className="mesh-invite-content" data-testid={`invite-reason-${phase.reason}`}>
        <p>{t(`invite.reason.${phase.reason}.description`)}</p>
        <Link to="/">{t('invite.backHome')}</Link>
      </div>,
    );
  }

  if (phase.kind === 'accepted') {
    const { result } = phase;
    return renderShell(
      t('invite.acceptedTitle'),
      <div className="mesh-invite-content" data-testid="invite-accepted">
        <p>{t('invite.acceptedDescription', { workspace: result.workspace.name })}</p>
        <Button data-testid="invite-enter" onClick={() => navigate(`/w/${result.workspace.slug}`)}>
          {t('invite.enterWorkspace', { workspace: result.workspace.name })}
        </Button>
      </div>,
    );
  }

  if (phase.kind === 'accepting') {
    return renderShell(
      t('title.invite'),
      <div className="mesh-invite-content" data-testid="invite-accepting" role="status">
        {t('common.loading')}
      </div>,
    );
  }

  const { preview } = phase;
  if (!preview.valid) {
    return renderShell(
      t(`invite.reason.${preview.reason}.title`),
      <div className="mesh-invite-content" data-testid={`invite-reason-${preview.reason}`}>
        <p>{t(`invite.reason.${preview.reason}.description`)}</p>
        <Link to="/">{t('invite.backHome')}</Link>
      </div>,
    );
  }

  const loggedIn = getToken() !== null;
  return renderShell(
    t('invite.previewTitle', { workspace: preview.workspace_name }),
    <div className="mesh-invite-content" data-testid="invite-preview">
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
    </div>,
  );
}
