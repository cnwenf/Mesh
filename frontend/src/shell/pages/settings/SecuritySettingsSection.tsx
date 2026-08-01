/**
 * 账号设置 → 安全(/settings/security,auth.md §4.2)。
 *
 * 拉取当前用户(fetchMe)后渲染 SecuritySettings(会话/两步验证/第三方绑定);
 * 未登录(无用户)不渲染安全区。卸载守卫:fetchMe 卸载后落定不再 setState。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchMe } from '../../../api/auth';
import type { CurrentUser } from '../../../api/auth';
import { getApiClient } from '../../../api/instance';
import { SettingsSection } from '../../../design';
import { SecuritySettings } from '../../../features/auth';
import { useT } from '../../../i18n';

export function SecuritySettingsSection(): React.JSX.Element {
  const t = useT();
  const client = getApiClient();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const reloadUser = useCallback(() => {
    void fetchMe(client)
      .then((me) => {
        if (isMountedRef.current) setUser(me);
      })
      .catch(() => {
        if (isMountedRef.current) setUser(null);
      });
  }, [client]);

  useEffect(() => {
    reloadUser();
  }, [reloadUser]);

  if (user === null) {
    return <div data-testid="security-pending" />;
  }

  return (
    <SettingsSection title={t('security.title')}>
      <SecuritySettings client={client} user={user} onUserChanged={reloadUser} />
    </SettingsSection>
  );
}
