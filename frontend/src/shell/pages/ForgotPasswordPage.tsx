/**
 * 忘记密码页(auth.md §4.1 / A4):输入账号邮箱发起重置。统一经 PublicFlowShell
 * 公共流程外壳呈现(design-quality §4.4)。恒成功呈现(防枚举);dev 下重置码入
 * Redis dev-mailbox,生产经 SMTP 发送。
 *
 * 错误处理(§9.1 原位提示 + 恢复动作;§9.2 不泄露账号存在性):服务端对任意合法格式
 * 邮箱恒返回成功,故 catch 仅命中传输/5xx 失败——呈现通用可操作错误 + 恢复建议,
 * 表单保留供重提交(恢复动作),且文案不涉及账号是否存在(防枚举)。标签页标题随语义变化(G19)。
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
  const [errorKey, setErrorKey] = useState<string | null>(null);

  useDocumentTitle(t('title.forgot'));

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      await forgotPassword(client, email.trim());
      setSent(true);
    } catch {
      // 通用可操作错误(不泄露账号存在性);恢复动作 = 保留表单重提交。
      setErrorKey('auth.forgotError');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <PublicFlowShell
      brandLabel={t('brand.name')}
      brandHref="/"
      skipLabel={t('a11y.skipLink')}
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
          {errorKey !== null ? (
            <div className="mesh-public-flow__alert" role="alert">
              <p className="mesh-public-flow__alert-message" data-testid="forgot-error">
                {t(errorKey)}
              </p>
              <p className="mesh-public-flow__alert-help">{t('auth.forgotErrorHelp')}</p>
            </div>
          ) : null}
          <Button data-testid="forgot-submit" type="submit" size="lg" isLoading={isSubmitting}>
            {t('forgot.submit')}
          </Button>
        </form>
      )}
    </PublicFlowShell>
  );
}
