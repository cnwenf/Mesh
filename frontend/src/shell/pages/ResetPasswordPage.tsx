/**
 * 重置密码页(auth.md §4.1 / A4):凭邮件中的重置码设新密码(并使旧会话失效)。
 * 统一经 PublicFlowShell 公共流程外壳呈现(design-quality §4.4)。重置码经 URL
 * `?token=` 传入;无效/过期令牌呈现具名错误并给恢复出口(重新发起重置);成功后
 * 引导回登录。标签页标题随语义变化(G19)。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { getApiClient } from '../../api/instance';
import { resetPassword } from '../../api/auth';
import { Button, Input, PublicFlowShell } from '../../design';
import { PasswordStrengthMeter } from '../../features/auth/PasswordStrengthMeter';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { useT } from '../../i18n';

export interface ResetPasswordPageProps {
  client?: MeshApiClient;
}

export function ResetPasswordPage(props: ResetPasswordPageProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const t = useT();
  const [searchParams] = useSearchParams();
  const [token, setToken] = useState(searchParams.get('token') ?? '');
  const [newPassword, setNewPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  useDocumentTitle(t('title.reset'));

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      await resetPassword(client, token.trim(), newPassword);
      setDone(true);
    } catch (err) {
      if (err instanceof MeshApiError && err.code === 'weak_password') {
        const reason = (err.details ?? {}).reason;
        setErrorKey(
          reason === 'too_short'
            ? 'auth.weakPasswordShort'
            : reason === 'needs_letter_and_digit'
              ? 'auth.weakPasswordLetterDigit'
              : 'auth.weakPasswordCommon',
        );
      } else {
        setErrorKey('reset.invalidToken');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const footer = (
    <Link to="/login" className="mesh-public-flow__inline-link" data-testid="reset-back">
      {t('forgot.backToLogin')}
    </Link>
  );

  if (done) {
    return (
      <PublicFlowShell
        brandLabel={t('brand.name')}
        brandHref="/"
        skipLabel={t('a11y.skipLink')}
        title={t('reset.title')}
        footer={footer}
      >
        <p className="mesh-public-flow__result" role="status" data-testid="reset-done">
          {t('reset.done')}
        </p>
      </PublicFlowShell>
    );
  }

  return (
    <PublicFlowShell
      brandLabel={t('brand.name')}
      brandHref="/"
      skipLabel={t('a11y.skipLink')}
      title={t('reset.title')}
      footer={footer}
    >
      <form className="mesh-public-flow__form" onSubmit={(event) => void handleSubmit(event)}>
        <Input
          data-testid="reset-code"
          label={t('reset.codeLabel')}
          value={token}
          size="lg"
          autoFocus
          autoComplete="off"
          onChange={(event) => setToken(event.target.value)}
        />
        <Input
          data-testid="reset-password"
          label={t('reset.newPasswordLabel')}
          type="password"
          value={newPassword}
          size="lg"
          autoComplete="new-password"
          onChange={(event) => setNewPassword(event.target.value)}
        />
        <PasswordStrengthMeter password={newPassword} />
        {errorKey !== null ? (
          <div className="mesh-public-flow__alert" role="alert">
            <p className="mesh-public-flow__alert-message" data-testid="reset-error">
              {t(errorKey)}
            </p>
            {errorKey === 'reset.invalidToken' ? (
              <Link
                to="/forgot"
                className="mesh-public-flow__inline-link"
                data-testid="reset-request-new"
              >
                {t('forgot.submit')}
              </Link>
            ) : null}
          </div>
        ) : null}
        <Button data-testid="reset-submit" type="submit" size="lg" isLoading={isSubmitting}>
          {t('reset.submit')}
        </Button>
      </form>
    </PublicFlowShell>
  );
}
