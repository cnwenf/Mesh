/**
 * 登录页(auth.md §3.1 / §4.1 接通 auth 后端):邮箱/密码登录 + 注册切换 + MFA 二步
 * + 第三方登录按钮组 + 注册结果态。统一经 PublicFlowShell 公共流程外壳呈现
 * (design-quality §4.4:品牌区 + 单任务卡 + 安全·帮助信息;§3.2 认证行)。
 *
 * - 登录成功将 access JWT(+ refresh)写入 authStore 并回跳 `?next=` 或首页;
 * - 启用 MFA 的账号:登录返回 mfa_required + ticket,本页进入二步验证码界面
 *   (TOTP / 备用码),`mfaVerify` 换会话凭证(§4.5 step 5);界面显示步骤/目标/恢复路径;
 * - 注册成功自动登录(不阻塞登录态),呈现「已发验证邮件」结果页,「继续」入口回跳;
 * - 「使用第三方账号登录」按钮组:按 env.oauthProviders 渲染(vendor 中立;
 *   dev 走 mock 提供商),点击导航到后端 `start`(redirect_uri 指向前端回调路由,
 *   与后端 M1 redirect_uri 白名单精确协同),回跳路径经 sessionStorage 携带;
 * - 409 conflict / 400 weak_password(三 reason)/ 422 invalid_credentials /
 *   423 account_locked / 429 rate_limited 均具名呈现(§6.14);错误可操作:贴近字段、
 *   告知怎么办,密码字段失败不清空(§9.2);账号锁定/凭据错误/网络错误分开提示;
 * - 移动端正确 inputmode / autocomplete / 键盘 Next·Go;首屏聚焦首个可编辑字段(§9.2);
 * - 标签页标题随模式/步骤语义变化(G19);
 * - 「忘记密码」跳 /forgot;「记住我」延长 refresh。
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
import { Button, Input, PublicFlowShell } from '../../design';
import { env } from '../../env';
import { PasswordStrengthMeter } from '../../features/auth/PasswordStrengthMeter';
import { safeNextPath } from '../../features/auth/safeNextPath';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { useT } from '../../i18n';
import { useAuthStore } from '../../state/authStore';

type AuthMode = 'login' | 'register';

/** 目录内置本地名的提供商键(其余 ID vendor 中立地原样展示,不绑定厂商) */
const PROVIDER_LABEL_KEYS: Readonly<Record<string, string>> = {
  mock: 'auth.oauthProvider.mock',
};

/**
 * 错误 → 可操作恢复提示映射(§9.2:告诉用户怎么办;账号锁定/凭据/限流/网络分开)。
 * 未列出的错误码不给附加提示(其本文文案已含恢复语义,如弱口令三 reason)。
 */
const ERROR_HELP_KEYS: Readonly<Record<string, string>> = {
  'auth.invalidCredentials': 'auth.invalidCredentialsHelp',
  'auth.accountLocked': 'auth.accountLockedHelp',
  'auth.rateLimited': 'auth.rateLimitedHelp',
  'error.network': 'auth.networkHelp',
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

/** 内联错误提示(role=alert):message 单独成元素(textContent 精确),help 为兄弟节点。 */
function AuthErrorAlert(props: { errorKey: string; helpKey: string | null }): React.JSX.Element {
  const t = useT();
  return (
    <div className="mesh-public-flow__alert" role="alert">
      <p className="mesh-public-flow__alert-message" data-testid="login-error">
        {t(props.errorKey)}
      </p>
      {props.helpKey !== null ? (
        <p className="mesh-public-flow__alert-help">{t(props.helpKey)}</p>
      ) : null}
    </div>
  );
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

  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [remember, setRemember] = useState(false);
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
  // 规范参数为 `?next=`(auth.md §4.1;路由守卫 / OAuth 往返 / 邀请接受共用);
  // `?redirect=` 作同义别名一并受理(MES-106),二者同经 safeNextPath 守卫。
  const target = safeNextPath(searchParams.get('next') ?? searchParams.get('redirect'));

  // 标签页标题随模式/步骤语义变化(G19):MFA / 注册 / 登录。
  const docTitleKey =
    mfaTicket !== null ? 'title.mfa' : mode === 'register' ? 'title.register' : 'title.login';
  useDocumentTitle(t(docTitleKey));

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

  const brandLabel = t('brand.name');
  const footer = (
    <>
      <p>{t('auth.footerSecurity')}</p>
      <p>{t('auth.footerHelp')}</p>
    </>
  );
  const errorHelpKey = errorKey !== null ? (ERROR_HELP_KEYS[errorKey] ?? null) : null;

  if (mfaTicket !== null) {
    return (
      <PublicFlowShell
        brandLabel={brandLabel}
        brandHref="/"
        title={t('auth.mfaTitle')}
        description={t('auth.mfaPrompt')}
        footer={footer}
      >
        <p className="mesh-public-flow__step" data-testid="mfa-step">
          {t('auth.mfaStep')} · {t('auth.mfaTarget')}
        </p>
        <form className="mesh-public-flow__form" onSubmit={(event) => void handleMfaSubmit(event)}>
          <Input
            data-testid="mfa-code"
            label={t('auth.mfaCodeLabel')}
            value={mfaCode}
            size="lg"
            autoFocus
            inputMode="numeric"
            autoComplete="one-time-code"
            onChange={(event) => setMfaCode(event.target.value)}
          />
          {errorKey !== null ? <AuthErrorAlert errorKey={errorKey} helpKey={errorHelpKey} /> : null}
          <Button data-testid="mfa-submit" type="submit" size="lg" isLoading={isSubmitting}>
            {t('auth.mfaVerifySubmit')}
          </Button>
        </form>
        <p className="mesh-public-flow__recovery">{t('auth.mfaRecovery')}</p>
      </PublicFlowShell>
    );
  }

  if (registeredEmail !== null) {
    return (
      <PublicFlowShell
        brandLabel={brandLabel}
        brandHref="/"
        title={t('auth.verifyEmailTitle')}
        footer={footer}
      >
        <p className="mesh-public-flow__result" data-testid="register-verify-sent">
          {t('auth.verifyEmailSent', { email: registeredEmail })}
        </p>
        <p className="mesh-public-flow__hint">{t('auth.verifyEmailNote')}</p>
        <Button data-testid="register-continue" size="lg" onClick={() => navigate(target)}>
          {t('auth.verifyEmailContinue')}
        </Button>
      </PublicFlowShell>
    );
  }

  return (
    <PublicFlowShell
      brandLabel={brandLabel}
      brandHref="/"
      title={t('login.title')}
      description={t('login.description')}
      footer={footer}
    >
      <div role="tablist" aria-label={t('auth.modeLabel')} className="mesh-public-flow__modes">
        <button
          type="button"
          role="tab"
          className="mesh-public-flow__mode"
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
          className="mesh-public-flow__mode"
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

      <form className="mesh-public-flow__form" onSubmit={(event) => void handleSubmit(event)}>
        {mode === 'register' ? (
          <Input
            data-testid="login-display-name"
            label={t('auth.displayNameLabel')}
            value={displayName}
            maxLength={80}
            size="lg"
            autoFocus
            autoComplete="name"
            onChange={(event) => setDisplayName(event.target.value)}
          />
        ) : null}
        <Input
          data-testid="login-email"
          label={t('auth.emailLabel')}
          type="email"
          value={email}
          size="lg"
          autoFocus={mode === 'login'}
          inputMode="email"
          autoComplete="email"
          onChange={(event) => setEmail(event.target.value)}
        />
        <Input
          data-testid="login-password"
          label={t('auth.passwordLabel')}
          type="password"
          value={password}
          size="lg"
          autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
          onChange={(event) => setPassword(event.target.value)}
        />
        {mode === 'register' ? <PasswordStrengthMeter password={password} /> : null}
        {mode === 'login' ? (
          <label className="mesh-public-flow__remember">
            <input
              type="checkbox"
              data-testid="login-remember"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            />
            {t('auth.remember')}
          </label>
        ) : null}
        {errorKey !== null ? <AuthErrorAlert errorKey={errorKey} helpKey={errorHelpKey} /> : null}
        <Button data-testid="login-account-submit" type="submit" size="lg" isLoading={isSubmitting}>
          {mode === 'register' ? t('auth.registerSubmit') : t('auth.loginSubmit')}
        </Button>
        {mode === 'login' ? (
          <Link to="/forgot" className="mesh-public-flow__inline-link" data-testid="login-forgot">
            {t('auth.forgotPassword')}
          </Link>
        ) : null}
      </form>

      {providers.length > 0 ? (
        <div className="mesh-public-flow__oauth">
          <p className="mesh-public-flow__divider" aria-hidden="true">
            {t('auth.oauthDivider')}
          </p>
          <div
            role="group"
            aria-label={t('auth.oauthContinue')}
            className="mesh-public-flow__actions"
          >
            {providers.map((provider) => (
              <Button
                key={provider}
                variant="secondary"
                size="lg"
                data-testid={`oauth-provider-${provider}`}
                onClick={() => handleOAuthStart(provider)}
              >
                {providerLabel(provider)}
              </Button>
            ))}
          </div>
        </div>
      ) : null}
    </PublicFlowShell>
  );
}
