/**
 * 重置密码页(auth.md §4.1 / A4):凭邮件中的重置码设新密码(并使旧会话失效)。
 * 重置码经 URL `?token=` 传入;无效/过期令牌呈现具名错误;成功后引导回登录。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { getApiClient } from '../../api/instance';
import { resetPassword } from '../../api/auth';
import { Button, Input } from '../../design';
import { PasswordStrengthMeter } from '../../features/auth/PasswordStrengthMeter';
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

  return (
    <div className="mesh-login">
      <h1 className="mesh-login__title">{t('reset.title')}</h1>
      {done ? (
        <>
          <p role="status" data-testid="reset-done">
            {t('reset.done')}
          </p>
          <Link to="/login">{t('forgot.backToLogin')}</Link>
        </>
      ) : (
        <form className="mesh-login__form" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            data-testid="reset-code"
            label={t('reset.codeLabel')}
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
          <Input
            data-testid="reset-password"
            label={t('reset.newPasswordLabel')}
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
          <PasswordStrengthMeter password={newPassword} />
          {errorKey !== null ? (
            <p role="alert" data-testid="reset-error">
              {t(errorKey)}
            </p>
          ) : null}
          <Button data-testid="reset-submit" type="submit" isLoading={isSubmitting}>
            {t('reset.submit')}
          </Button>
        </form>
      )}
    </div>
  );
}
