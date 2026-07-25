/**
 * 登录占位页(骨架阶段;真实 auth 归阶段 2 auth.md)。
 * 提交仅将粘帖的 token 写入 authStore 供联调,随后导航回首页;
 * 已登录(有 token)时重定向到 /。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { Button, Input } from '../../design';
import { useT } from '../../i18n';
import { useAuthStore } from '../../state/authStore';

export function LoginPage(): React.JSX.Element {
  const t = useT();
  const navigate = useNavigate();
  const token = useAuthStore((state) => state.token);
  const setToken = useAuthStore((state) => state.setToken);
  const [value, setValue] = useState('');

  if (token !== null) {
    return <Navigate to="/" replace />;
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = value.trim();
    if (trimmed.length === 0) return;
    setToken(trimmed);
    navigate('/');
  };

  return (
    <div className="mesh-login">
      <h1 className="mesh-login__title">{t('login.title')}</h1>
      <p className="mesh-login__description">{t('login.description')}</p>
      <p className="mesh-login__phase">{t('login.phaseNote')}</p>
      <form className="mesh-login__form" onSubmit={handleSubmit}>
        <Input
          data-testid="login-token"
          label={t('login.tokenLabel')}
          placeholder={t('login.tokenPlaceholder')}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <Button data-testid="login-submit" type="submit">
          {t('login.submit')}
        </Button>
      </form>
    </div>
  );
}
