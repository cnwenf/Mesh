/**
 * 忘记密码页(auth.md §4.1 / A4):输入账号邮箱发起重置。统一经 PublicFlowShell
 * 公共流程外壳呈现(design-quality §4.4)。恒成功呈现(防枚举);dev 下重置码入
 * Redis dev-mailbox,生产经 SMTP 发送。标签页标题随语义变化(G19)。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router';
import type { MeshApiClient } from '../../api/client';
import { getApiClient } from '../../api/instance';
import { forgotPassword } from '../../api/auth';
import { Button, Input, PublicFlowShell } from '../../design';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
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

  useDocumentTitle(t('title.forgot'));

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
    <PublicFlowShell
      brandLabel={t('brand.name')}
      brandHref="/"
      title={t('forgot.title')}
      description={t('forgot.description')}
      footer={
        <>
          <Link to="/login" className="mesh-public-flow__inline-link" data-testid="forgot-back">
            {t('forgot.backToLogin')}
          </Link>
          <p>{t('auth.footerSecurity')}</p>
        </>
      }
    >
      {sent ? (
        <p className="mesh-public-flow__result" role="status" data-testid="forgot-sent">
          {t('forgot.sent')}
        </p>
      ) : (
        <form className="mesh-public-flow__form" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            data-testid="forgot-email"
            label={t('auth.emailLabel')}
            type="email"
            value={email}
            size="lg"
            autoFocus
            inputMode="email"
            autoComplete="email"
            onChange={(event) => setEmail(event.target.value)}
          />
          <Button data-testid="forgot-submit" type="submit" size="lg" isLoading={isSubmitting}>
            {t('forgot.submit')}
          </Button>
        </form>
      )}
    </PublicFlowShell>
  );
}
