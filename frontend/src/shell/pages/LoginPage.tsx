/**
 * 登录页(auth.md §3.1 / §4.1 接通 auth 后端):邮箱/密码登录 + 注册切换 + MFA 二步
 * + 第三方登录按钮组 + 注册结果态。
 *
 * - 登录成功将 access JWT(+ refresh)写入 authStore 并回跳 `?next=` 或首页;
 * - 启用 MFA 的账号:登录返回 mfa_required + ticket,本页进入二步验证码界面
 *   (TOTP / 备用码),`mfaVerify` 换会话凭证(§4.5 step 5);
 * - 注册成功自动登录(不阻塞登录态),呈现「已发验证邮件」结果页,「继续」入口回跳;
 * - 「使用第三方账号登录」按钮组:按 env.oauthProviders 渲染(vendor 中立;
 *   dev 走 mock 提供商),点击导航到后端 `start`(redirect_uri 指向前端回调路由,
 *   与后端 M1 redirect_uri 白名单精确协同),回跳路径经 sessionStorage 携带;
 * - 409 conflict / 400 weak_password(三 reason)/ 422 invalid_credentials /
 *   423 account_locked / 429 rate_limited 均具名呈现(§6.14);
 * - 「忘记密码」跳 /forgot;「记住我」延长 refresh;开发用 token 直填入口保留。
 */
import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { getApiClient } from '../../api/instance';
import { isSessionTokens, login, mfaVerify, register } from '../../api/auth';
import type { SessionTokens } from '../../api/auth';
import { OAUTH_NEXT_STORAGE_KEY, oauthLoginUrl, oauthRedirectUri } from '../../api/oauth';
import { Button, Input } from '../../design';
import { env } from '../../env';
import { PasswordStrengthMeter } from '../../features/auth/PasswordStrengthMeter';
import { safeNextPath } from '../../features/auth/safeNextPath';
import { useT } from '../../i18n';
import { useAuthStore } from '../../state/authStore';

type AuthMode = 'login' | 'register';

/** 目录内置本地名的提供商键(其余 ID vendor 中立地原样展示,不绑定厂商) */
const PROVIDER_LABEL_KEYS: Readonly<Record<string, string>> = {
  mock: 'auth.oauthProvider.mock',
};

/** 目录无本地名时的回退展示:首字母大写的提供商 ID */
function fallbackProviderLabel(provider: string): string {
  return provider.length === 0 ? provider : provider[0].toUpperCase() + provider.slice(1);
}

export interface LoginPageProps {
  client?: MeshApiClient;
  /** 第三方登录按钮组渲染的提供商 ID(默认 env.oauthProviders) */
  oauthProviders?: readonly string[];
  /** OAuth start 端点所在 API 基址(默认 env.apiBaseUrl) */
  apiBaseUrl?: string;
  /** 第三方登录导航副作用(默认 window.location.assign;测试可注入断言) */
  onOAuthStart?: (url: string) => void;
}

export function LoginPage(props: LoginPageProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const providers = props.oauthProviders ?? env.oauthProviders;
  const apiBaseUrl = props.apiBaseUrl ?? env.apiBaseUrl;
  const onOAuthStart = props.onOAuthStart ?? ((url: string) => window.location.assign(url));
  const t = useT();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = useAuthStore((state) => state.token);
  const setSession = useAuthStore((state) => state.setSession);
  const setToken = useAuthStore((state) => state.setToken);

  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [remember, setRemember] = useState(false);
  const [devToken, setDevToken] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  // MFA 二步:登录返回质询后保存 ticket,进入验证码界面。
  const [mfaTicket, setMfaTicket] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState('');

  // 注册结果态(§4.1):自动登录态已写入,呈现「已发验证邮件」结果页而非直接跳转。
  const [registeredEmail, setRegisteredEmail] = useState<string | null>(null);
  // 结果态的同步镜像:写会话(zustand 外部 store)可能先于组件 state 刷新触发
  // 同步重渲(真实浏览器无 act 批处理),守卫读 ref 防止 <Navigate> 抢先跳走。
  const registeredResultActive = useRef(false);

  // 回跳目标站内路径守卫(防开放重定向,`//` 与 `/\` 反斜杠变体同款拒绝)。
  const target = safeNextPath(searchParams.get('next'));

  if (token !== null && !registeredResultActive.current) {
    return <Navigate to={target} replace />;
  }

  const finish = (tokens: SessionTokens): void => {
    setSession({ accessToken: tokens.access_token });
    navigate(target);
  };

  const errorToKey = (err: unknown): string => {
    if (err instanceof MeshApiError) {
      if (err.code === 'weak_password') {
        const reason = (err.details ?? {}).reason;
        return reason === 'too_short'
          ? 'auth.weakPasswordShort'
          : reason === 'needs_letter_and_digit'
            ? 'auth.weakPasswordLetterDigit'
            : 'auth.weakPasswordCommon';
      }
      if (err.code === 'invalid_credentials') return 'auth.invalidCredentials';
      if (err.code === 'conflict') return 'auth.emailTaken';
      if (err.code === 'account_locked') return 'auth.accountLocked';
      if (err.code === 'rate_limited') return 'auth.rateLimited';
      return `error.${err.code}`;
    }
    return 'error.network';
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
      const result = await login(client, { email: email.trim(), password, remember });
      if (!isSessionTokens(result)) {
        // MFA 质询:进入二步验证码界面。
        setMfaTicket(result.mfa_ticket);
        return;
      }
      if (mode === 'register') {
        // §4.1 注册结果态:先同步置位守卫镜像,再写会话(zustand 外部 store
        // 变更可能先于组件 state 刷新触发同步重渲)。
        registeredResultActive.current = true;
        setRegisteredEmail(email.trim());
        setSession({ accessToken: result.access_token });
        return;
      }
      finish(result);
    } catch (err) {
      setErrorKey(errorToKey(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleMfaSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (mfaTicket === null) return;
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      const tokens = await mfaVerify(client, mfaTicket, mfaCode.trim());
      finish(tokens);
    } catch (err) {
      if (err instanceof MeshApiError && err.code === 'invalid_credentials') {
        setErrorKey('auth.mfaInvalidCode');
      } else {
        setErrorKey(errorToKey(err));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  /**
   * 第三方登录往返(§4.5 step 5):回跳目标先存 sessionStorage(redirect_uri
   * 须与后端 M1 精确白名单一致,不能携带易变查询串),再导航到后端 start。
   */
  const handleOAuthStart = (provider: string): void => {
    sessionStorage.setItem(OAUTH_NEXT_STORAGE_KEY, target);
    onOAuthStart(oauthLoginUrl(apiBaseUrl, provider, oauthRedirectUri(provider)));
  };

  const providerLabel = (provider: string): string => {
    const key = PROVIDER_LABEL_KEYS[provider];
    return key !== undefined ? t(key) : fallbackProviderLabel(provider);
  };

  const handleDevTokenSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = devToken.trim();
    if (trimmed.length === 0) return;
    setToken(trimmed);
    navigate(target);
  };

  if (mfaTicket !== null) {
    return (
      <div className="mesh-login">
        <h1 className="mesh-login__title">{t('auth.mfaTitle')}</h1>
        <p className="mesh-login__description">{t('auth.mfaPrompt')}</p>
        <form className="mesh-login__form" onSubmit={(event) => void handleMfaSubmit(event)}>
          <Input
            data-testid="mfa-code"
            label={t('auth.mfaCodeLabel')}
            value={mfaCode}
            onChange={(event) => setMfaCode(event.target.value)}
          />
          {errorKey !== null ? (
            <p role="alert" data-testid="login-error">
              {t(errorKey)}
            </p>
          ) : null}
          <Button data-testid="mfa-submit" type="submit" isLoading={isSubmitting}>
            {t('auth.mfaVerifySubmit')}
          </Button>
        </form>
      </div>
    );
  }

  if (registeredEmail !== null) {
    return (
      <div className="mesh-login">
        <h1 className="mesh-login__title">{t('auth.verifyEmailTitle')}</h1>
        <p className="mesh-login__description" data-testid="register-verify-sent">
          {t('auth.verifyEmailSent', { email: registeredEmail })}
        </p>
        <p className="mesh-login__hint">{t('auth.verifyEmailNote')}</p>
        <Button data-testid="register-continue" onClick={() => navigate(target)}>
          {t('auth.verifyEmailContinue')}
        </Button>
      </div>
    );
  }

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
        {mode === 'register' ? <PasswordStrengthMeter password={password} /> : null}
        {mode === 'login' ? (
          <label className="mesh-login__remember">
            <input
              type="checkbox"
              data-testid="login-remember"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            />
            {t('auth.remember')}
          </label>
        ) : null}
        {errorKey !== null ? (
          <p role="alert" data-testid="login-error">
            {t(errorKey)}
          </p>
        ) : null}
        <Button data-testid="login-account-submit" type="submit" isLoading={isSubmitting}>
          {mode === 'register' ? t('auth.registerSubmit') : t('auth.loginSubmit')}
        </Button>
        {mode === 'login' ? (
          <Link to="/forgot" data-testid="login-forgot">
            {t('auth.forgotPassword')}
          </Link>
        ) : null}
      </form>

      {providers.length > 0 ? (
        <div className="mesh-login__oauth">
          <p className="mesh-login__divider" aria-hidden="true">
            {t('auth.oauthDivider')}
          </p>
          <div role="group" aria-label={t('auth.oauthContinue')} className="mesh-login__oauth-buttons">
            {providers.map((provider) => (
              <Button
                key={provider}
                variant="secondary"
                data-testid={`oauth-provider-${provider}`}
                onClick={() => handleOAuthStart(provider)}
              >
                {providerLabel(provider)}
              </Button>
            ))}
          </div>
        </div>
      ) : null}

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
