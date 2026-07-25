/**
 * 登录页(auth.md §3.1 接通 v0.2.0 后端):邮箱/密码登录 + 注册切换。
 *
 * - 登录成功将 access JWT 写入 authStore 并回跳 `?next=`(邀请接受页等)或首页;
 * - 注册成功自动登录;409 conflict(邮箱占用)/ 400 weak_password(三 reason)/
 *   422 invalid_credentials / MFA 质询均具名呈现(§6.14);
 * - 开发用 token 直填入口保留(mock e2e 与 dev-token 联调,`login-token`/`login-submit`)。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { getApiClient } from '../../api/instance';
import { isSessionTokens, login, register } from '../../api/auth';
import { Button, Input } from '../../design';
import { useT } from '../../i18n';
import { useAuthStore } from '../../state/authStore';

type AuthMode = 'login' | 'register';

export interface LoginPageProps {
  client?: MeshApiClient;
}

export function LoginPage(props: LoginPageProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const t = useT();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = useAuthStore((state) => state.token);
  const setToken = useAuthStore((state) => state.setToken);

  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [devToken, setDevToken] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  const nextPath = searchParams.get('next');
  const target = nextPath !== null && nextPath.startsWith('/') && !nextPath.startsWith('//')
    ? nextPath
    : '/';

  if (token !== null) {
    return <Navigate to={target} replace />;
  }

  const finish = (accessToken: string): void => {
    setToken(accessToken);
    navigate(target);
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      if (mode === 'register') {
        await register(client, {
          email: email.trim(),
          password,
          display_name: displayName.trim(),
        });
      }
      const result = await login(client, { email: email.trim(), password });
      if (!isSessionTokens(result)) {
        setErrorKey('auth.mfaUnsupported');
        return;
      }
      finish(result.access_token);
    } catch (err) {
      if (err instanceof MeshApiError) {
        if (err.code === 'weak_password') {
          const reason = (err.details ?? {}).reason;
          setErrorKey(
            reason === 'too_short'
              ? 'auth.weakPasswordShort'
              : reason === 'needs_letter_and_digit'
                ? 'auth.weakPasswordLetterDigit'
                : 'auth.weakPasswordCommon',
          );
        } else if (err.code === 'invalid_credentials') {
          setErrorKey('auth.invalidCredentials');
        } else if (err.code === 'conflict') {
          setErrorKey('auth.emailTaken');
        } else {
          setErrorKey(`error.${err.code}`);
        }
      } else {
        setErrorKey('error.network');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDevTokenSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = devToken.trim();
    if (trimmed.length === 0) return;
    finish(trimmed);
  };

  return (
    <div className="mesh-login">
      <h1 className="mesh-login__title">{t('login.title')}</h1>
      <p className="mesh-login__description">{t('login.description')}</p>

      <div role="tablist" aria-label={t('auth.modeLabel')} className="mesh-login__modes">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'login'}
          data-testid="login-mode-login"
          onClick={() => {
            setMode('login');
            setErrorKey(null);
          }}
        >
          {t('auth.modeLogin')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'register'}
          data-testid="login-mode-register"
          onClick={() => {
            setMode('register');
            setErrorKey(null);
          }}
        >
          {t('auth.modeRegister')}
        </button>
      </div>

      <form className="mesh-login__form" onSubmit={(event) => void handleSubmit(event)}>
        {mode === 'register' ? (
          <Input
            data-testid="login-display-name"
            label={t('auth.displayNameLabel')}
            value={displayName}
            maxLength={80}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        ) : null}
        <Input
          data-testid="login-email"
          label={t('auth.emailLabel')}
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Input
          data-testid="login-password"
          label={t('auth.passwordLabel')}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {errorKey !== null ? (
          <p role="alert" data-testid="login-error">
            {t(errorKey)}
          </p>
        ) : null}
        <Button data-testid="login-account-submit" type="submit" isLoading={isSubmitting}>
          {mode === 'register' ? t('auth.registerSubmit') : t('auth.loginSubmit')}
        </Button>
      </form>

      <details className="mesh-login__dev">
        <summary>{t('login.phaseNote')}</summary>
        <form className="mesh-login__form" onSubmit={handleDevTokenSubmit}>
          <Input
            data-testid="login-token"
            label={t('login.tokenLabel')}
            placeholder={t('login.tokenPlaceholder')}
            value={devToken}
            onChange={(event) => setDevToken(event.target.value)}
          />
          <Button data-testid="login-submit" type="submit" variant="secondary">
            {t('login.submit')}
          </Button>
        </form>
      </details>
    </div>
  );
}
