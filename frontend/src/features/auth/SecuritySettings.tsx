/**
 * 设置 → 安全(auth.md §4.2):活跃会话(列出/撤销/全端登出)、两步验证
 * (TOTP 密钥 + 备用码 + 验证码确认启用/停用)、第三方账号绑定(列出/解绑,
 * 保留至少一种登录方式)。用户级(非工作区上下文)。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import {
  listIdentities,
  listSessions,
  logoutAll,
  mfaDisable,
  mfaEnable,
  mfaSetup,
  revokeSession,
  unbindIdentity,
} from '../../api';
import type { CurrentUser, MfaSetupInfo, OAuthIdentity, SessionInfo } from '../../api';
import { Button, Input } from '../../design';
import { useT } from '../../i18n';

export interface SecuritySettingsProps {
  client: MeshApiClient;
  user: CurrentUser;
  /** MFA 状态变更后回调父级刷新当前用户 */
  onUserChanged?: () => void;
}

export function SecuritySettings(props: SecuritySettingsProps): React.JSX.Element {
  const { client, user, onUserChanged } = props;
  const t = useT();

  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [identities, setIdentities] = useState<OAuthIdentity[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  // MFA 向导:setup 结果(密钥/备用码)+ 确认码;停用只需确认码。
  const [setup, setSetup] = useState<MfaSetupInfo | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [disabling, setDisabling] = useState(false);

  const reload = useCallback(() => {
    void listSessions(client)
      .then(setSessions)
      .catch(() => setSessions([]));
    void listIdentities(client)
      .then(setIdentities)
      .catch(() => setIdentities([]));
  }, [client]);

  useEffect(() => {
    reload();
  }, [reload]);

  const fail = (err: unknown, fallback: string): void => {
    if (err instanceof MeshApiError && err.code === 'last_login_method') {
      setErrorKey('security.oauthLastMethod');
    } else if (err instanceof MeshApiError && err.code === 'invalid_credentials') {
      setErrorKey('auth.mfaInvalidCode');
    } else {
      setErrorKey(fallback);
    }
  };

  const handleRevoke = async (sessionId: string): Promise<void> => {
    setErrorKey(null);
    try {
      await revokeSession(client, sessionId);
      setNotice(t('security.sessionRevoked'));
      reload();
    } catch (err) {
      fail(err, 'common.unknownError');
    }
  };

  const handleLogoutAll = async (): Promise<void> => {
    setErrorKey(null);
    try {
      await logoutAll(client);
      setNotice(t('security.sessionRevoked'));
      reload();
    } catch (err) {
      fail(err, 'common.unknownError');
    }
  };

  const handleStartEnable = async (): Promise<void> => {
    setErrorKey(null);
    try {
      const info = await mfaSetup(client);
      setSetup(info);
      setMfaCode('');
    } catch (err) {
      fail(err, 'common.unknownError');
    }
  };

  const handleConfirmEnable = async (): Promise<void> => {
    if (setup === null) return;
    setErrorKey(null);
    try {
      await mfaEnable(client, mfaCode.trim());
      setSetup(null);
      setMfaCode('');
      setNotice(t('security.mfaEnabled'));
      onUserChanged?.();
    } catch (err) {
      fail(err, 'common.unknownError');
    }
  };

  const handleStartDisable = (): void => {
    setErrorKey(null);
    setDisabling(true);
    setMfaCode('');
  };

  const handleConfirmDisable = async (): Promise<void> => {
    setErrorKey(null);
    try {
      await mfaDisable(client, mfaCode.trim());
      setDisabling(false);
      setMfaCode('');
      setNotice(t('security.mfaDisabled'));
      onUserChanged?.();
    } catch (err) {
      fail(err, 'common.unknownError');
    }
  };

  const handleUnbind = async (provider: string): Promise<void> => {
    setErrorKey(null);
    try {
      await unbindIdentity(client, provider);
      setNotice(t('security.oauthUnbound'));
      reload();
    } catch (err) {
      fail(err, 'common.unknownError');
    }
  };

  return (
    <div className="mesh-settings__group">
      {notice !== null ? (
        <p role="status" data-testid="security-notice">
          {notice}
        </p>
      ) : null}
      {errorKey !== null ? (
        <p role="alert" data-testid="security-error">
          {t(errorKey)}
        </p>
      ) : null}

      {/* 活跃会话 */}
      <section aria-label={t('security.sessions')}>
        <h3 className="mesh-settings__heading">{t('security.sessions')}</h3>
        {sessions.length === 0 ? (
          <p data-testid="sessions-empty">{t('security.sessionsEmpty')}</p>
        ) : (
          <ul className="mesh-security__sessions">
            {sessions.map((session) => (
              <li key={session.id} data-testid={`session-${session.id}`}>
                <span>{session.user_agent ?? session.type}</span>
                <span>{session.ip_address ?? ''}</span>
                {session.current ? <em>{t('security.sessionCurrent')}</em> : null}
                <Button
                  variant="secondary"
                  onClick={() => void handleRevoke(session.id)}
                  aria-label={`${t('security.sessionRevoke')} ${session.user_agent ?? session.id}`}
                >
                  {t('security.sessionRevoke')}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <Button variant="secondary" data-testid="logout-all" onClick={() => void handleLogoutAll()}>
          {t('security.logoutAll')}
        </Button>
      </section>

      {/* 两步验证 */}
      <section aria-label={t('security.mfa')}>
        <h3 className="mesh-settings__heading">{t('security.mfa')}</h3>
        {user.mfa_enabled ? (
          <>
            <p>{t('security.mfaOn')}</p>
            {disabling ? (
              <div className="mesh-security__mfa-confirm">
                <Input
                  data-testid="mfa-disable-code"
                  label={t('security.mfaConfirmLabel')}
                  value={mfaCode}
                  onChange={(event) => setMfaCode(event.target.value)}
                />
                <Button
                  data-testid="mfa-disable-confirm"
                  onClick={() => void handleConfirmDisable()}
                >
                  {t('security.mfaConfirmDisable')}
                </Button>
              </div>
            ) : (
              <Button
                variant="secondary"
                data-testid="mfa-disable"
                onClick={handleStartDisable}
              >
                {t('security.mfaDisable')}
              </Button>
            )}
          </>
        ) : setup === null ? (
          <>
            <p>{t('security.mfaOff')}</p>
            <Button data-testid="mfa-enable" onClick={() => void handleStartEnable()}>
              {t('security.mfaEnable')}
            </Button>
          </>
        ) : (
          <div className="mesh-security__mfa-setup" data-testid="mfa-setup">
            <p>
              {t('security.mfaSecretLabel')}: <code data-testid="mfa-secret">{setup.secret}</code>
            </p>
            <p>
              {t('security.mfaUriLabel')}: <code>{setup.otpauth_uri}</code>
            </p>
            <p>{t('security.mfaBackupCodes')}:</p>
            <ul data-testid="mfa-backup-codes">
              {setup.backup_codes.map((code) => (
                <li key={code}>
                  <code>{code}</code>
                </li>
              ))}
            </ul>
            <Input
              data-testid="mfa-enable-code"
              label={t('security.mfaConfirmLabel')}
              value={mfaCode}
              onChange={(event) => setMfaCode(event.target.value)}
            />
            <Button data-testid="mfa-enable-confirm" onClick={() => void handleConfirmEnable()}>
              {t('security.mfaConfirmEnable')}
            </Button>
          </div>
        )}
      </section>

      {/* 第三方账号绑定 */}
      <section aria-label={t('security.oauth')}>
        <h3 className="mesh-settings__heading">{t('security.oauth')}</h3>
        {identities.length === 0 ? (
          <p data-testid="oauth-empty">{t('security.oauthEmpty')}</p>
        ) : (
          <ul className="mesh-security__oauth">
            {identities.map((identity) => (
              <li key={identity.provider} data-testid={`oauth-${identity.provider}`}>
                <span>{identity.provider}</span>
                <span>{identity.provider_email ?? ''}</span>
                {/* §4.2:唯一登录方式时灰化解绑(服务端 last_login_method 兜底) */}
                <Button
                  variant="secondary"
                  data-testid={`oauth-unbind-${identity.provider}`}
                  disabled={identities.length <= 1}
                  title={identities.length <= 1 ? t('security.oauthLastMethod') : undefined}
                  onClick={() => void handleUnbind(identity.provider)}
                >
                  {t('security.oauthUnbind')}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
