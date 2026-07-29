/**
 * 设置 → 安全(auth.md §4.2):修改密码(旧+新+确认+强度条,实时校验)、
 * 活跃会话(列出/撤销/全端登出)、两步验证(TOTP 密钥 + 备用码 + 验证码确认
 * 启用/停用)、第三方账号绑定(列出/解绑,保留至少一种登录方式)。
 * 用户级(非工作区上下文)。
 */
import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import {
  ERROR_INVALID_CREDENTIALS,
  ERROR_WEAK_PASSWORD,
  changePassword,
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
import { PasswordStrengthMeter } from './PasswordStrengthMeter';

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

  // 修改密码(§4.2):折叠表单(旧+新+确认+强度条);呈递当前会话 refresh 使其保留。
  const [changing, setChanging] = useState(false);
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submittingPassword, setSubmittingPassword] = useState(false);
  const confirmMismatch = confirmPassword.length > 0 && newPassword !== confirmPassword;

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

  const handleChangePassword = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setErrorKey(null);
    // 确认密码不一致时客户端拦截(服务端不接收确认值);强度由服务端权威裁定,
    // 故不据客户端评估禁用提交(与 PasswordStrengthMeter 策略一致)。
    if (newPassword !== confirmPassword) {
      setErrorKey('security.confirmMismatch');
      return;
    }
    setSubmittingPassword(true);
    try {
      await changePassword(client, { oldPassword, newPassword });
      setNotice(t('security.changePasswordSuccess'));
      setChanging(false);
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
      reload(); // 其它会话已失效,刷新会话列表使其收敛(§4.2)
      onUserChanged?.();
    } catch (err) {
      if (err instanceof MeshApiError && err.code === ERROR_INVALID_CREDENTIALS) {
        setErrorKey('security.wrongOldPassword');
      } else if (err instanceof MeshApiError && err.code === ERROR_WEAK_PASSWORD) {
        const reason = (err.details ?? {}).reason;
        setErrorKey(
          reason === 'too_short'
            ? 'auth.weakPasswordShort'
            : reason === 'needs_letter_and_digit'
              ? 'auth.weakPasswordLetterDigit'
              : 'auth.weakPasswordCommon',
        );
      } else {
        setErrorKey('common.unknownError');
      }
    } finally {
      setSubmittingPassword(false);
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

      {/* 修改密码(auth.md §4.2:旧+新+强度条) */}
      <section aria-label={t('security.changePassword')}>
        <h3 className="mesh-settings__heading">{t('security.changePassword')}</h3>
        {!changing ? (
          <Button
            variant="secondary"
            data-testid="change-password-toggle"
            aria-expanded={false}
            onClick={() => {
              setErrorKey(null);
              setChanging(true);
            }}
          >
            {t('security.changePassword')}
          </Button>
        ) : (
          <form
            className="mesh-security__change-password"
            data-testid="change-password-form"
            onSubmit={(event) => void handleChangePassword(event)}
          >
            <Input
              data-testid="cp-old"
              label={t('security.oldPasswordLabel')}
              type="password"
              autoComplete="current-password"
              value={oldPassword}
              onChange={(event) => setOldPassword(event.target.value)}
            />
            <Input
              data-testid="cp-new"
              label={t('security.newPasswordLabel')}
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
            <PasswordStrengthMeter password={newPassword} />
            <Input
              data-testid="cp-confirm"
              label={t('security.confirmPasswordLabel')}
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
            {confirmMismatch ? (
              <p role="alert" data-testid="cp-mismatch">
                {t('security.confirmMismatch')}
              </p>
            ) : null}
            <Button
              data-testid="cp-submit"
              type="submit"
              disabled={confirmMismatch}
              isLoading={submittingPassword}
            >
              {t('security.changePasswordSubmit')}
            </Button>
          </form>
        )}
      </section>

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
