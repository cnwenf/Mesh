/**
 * OAuth 登录回调页(auth.md §4.1 / §4.5 step 5):提供商 302 回跳至此,
 * 携 code+state;经后端 callback 端点交换会话凭证写入 authStore,随后回跳
 * 登录前 `?next=` 目标(往返间存于 sessionStorage,避免污染 M1 精确白名单
 * 的 redirect_uri)或首页。交换失败具名呈现(无效 state / redirect 未授权),
 * 并提供「返回登录」入口。vendor 中立:provider 仅作为 URL 安全 slug 透传。
 */
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { getApiClient } from '../../api/instance';
import {
  ERROR_INVALID_OAUTH_STATE,
  ERROR_REDIRECT_NOT_ALLOWED,
  OAUTH_NEXT_STORAGE_KEY,
  oauthCallbackLogin,
} from '../../api/oauth';
import { useT } from '../../i18n';
import { useAuthStore } from '../../state/authStore';

export interface OAuthCallbackPageProps {
  client?: MeshApiClient;
}

/** 站内路径校验(与 LoginPage 同策:仅 `/` 开头且非协议相对,防开放重定向) */
function safeNext(raw: string | null): string {
  if (raw !== null && raw.startsWith('/') && !raw.startsWith('//')) {
    return raw;
  }
  return '/';
}

export function OAuthCallbackPage(props: OAuthCallbackPageProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const t = useT();
  const navigate = useNavigate();
  const { provider } = useParams();
  const [searchParams] = useSearchParams();
  const setSession = useAuthStore((state) => state.setSession);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    // StrictMode 双调用守卫:state 为一次性(后端消费即删),交换只能发起一次。
    if (started.current) return;
    started.current = true;
    const code = searchParams.get('code');
    const state = searchParams.get('state');
    if (provider === undefined || code === null || state === null) {
      setErrorKey('auth.oauthStateInvalid');
      return;
    }
    void oauthCallbackLogin(client, provider, code, state)
      .then((tokens) => {
        setSession({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
        const next = safeNext(sessionStorage.getItem(OAUTH_NEXT_STORAGE_KEY));
        sessionStorage.removeItem(OAUTH_NEXT_STORAGE_KEY);
        navigate(next, { replace: true });
      })
      .catch((err: unknown) => {
        if (err instanceof MeshApiError && err.code === ERROR_INVALID_OAUTH_STATE) {
          setErrorKey('auth.oauthStateInvalid');
        } else if (err instanceof MeshApiError && err.code === ERROR_REDIRECT_NOT_ALLOWED) {
          setErrorKey('auth.oauthRedirectNotAllowed');
        } else {
          setErrorKey('common.unknownError');
        }
      });
  }, [client, provider, searchParams, setSession, navigate]);

  if (errorKey !== null) {
    return (
      <div className="mesh-login">
        <h1 className="mesh-login__title">{t('auth.oauthFailedTitle')}</h1>
        <p role="alert" data-testid="oauth-callback-error">
          {t(errorKey)}
        </p>
        <Link to="/login" data-testid="oauth-callback-back">
          {t('auth.oauthBackToLogin')}
        </Link>
      </div>
    );
  }

  return (
    <div className="mesh-login">
      <p role="status" data-testid="oauth-callback-pending">
        {t('auth.oauthPending')}
      </p>
    </div>
  );
}
