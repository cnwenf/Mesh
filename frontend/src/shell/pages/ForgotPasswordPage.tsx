/**
 * 忘记密码页(auth.md §4.1 / A4):输入账号邮箱发起重置。
 * 恒成功呈现(防枚举);dev 下重置码入 Redis dev-mailbox,生产经 SMTP 发送。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router';
import type { MeshApiClient } from '../../api/client';
import { getApiClient } from '../../api/instance';
import { forgotPassword } from '../../api/auth';
import { Button, Input } from '../../design';
import { useT } from '../../i18n';

export interface ForgotPasswordPageProps {
  client?: MeshApiClient;
}

export function ForgotPasswordPage(props: ForgotPasswordPageProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const t = useT();
  const [email, setEmail] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setIsSubmitting(true);
    try {
      await forgotPassword(client, email.trim());
      setSent(true);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mesh-login">
      <h1 className="mesh-login__title">{t('forgot.title')}</h1>
      <p className="mesh-login__description">{t('forgot.description')}</p>
      {sent ? (
        <p role="status" data-testid="forgot-sent">
          {t('forgot.sent')}
        </p>
      ) : (
        <form className="mesh-login__form" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            data-testid="forgot-email"
            label={t('auth.emailLabel')}
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <Button data-testid="forgot-submit" type="submit" isLoading={isSubmitting}>
            {t('forgot.submit')}
          </Button>
        </form>
      )}
      <Link to="/login" data-testid="forgot-back">
        {t('forgot.backToLogin')}
      </Link>
    </div>
  );
}
