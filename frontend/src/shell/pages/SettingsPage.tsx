/**
 * 账号设置页(/settings,design-quality.md §4.4 Settings 模板 / §3.2 设置行)。
 *
 * SettingsLayout 二级导航(桌面左栏 / 手机顶部分组列表)+ 内容列;内容按子路由分页:
 *   /settings            → Navigate replace → /settings/appearance
 *   /settings/appearance → 主题三态 + 语言 + 时区(即时生效)
 *   /settings/notifications → 通知偏好矩阵
 *   /settings/security   → 会话 / 两步验证 / 第三方绑定
 * 子路由经 <Outlet /> 呈现;各分页组件自包含(读 store/上下文)。
 */
import { Outlet } from 'react-router';
import { SettingsLayout } from '../../design';
import type { SettingsNavGroup } from '../../design';
import { useT } from '../../i18n';
import { useDocumentTitle } from '../hooks';

export function SettingsPage(): React.JSX.Element {
  const t = useT();
  useDocumentTitle(t('settings.title')); // G19 标签页标题
  const groups: ReadonlyArray<SettingsNavGroup> = [
    {
      items: [
        {
          key: 'appearance',
          label: t('settings.appearance'),
          to: '/settings/appearance',
          icon: 'settings',
        },
        {
          key: 'notifications',
          label: t('notifications.title'),
          to: '/settings/notifications',
          icon: 'bell',
        },
        {
          key: 'security',
          label: t('security.title'),
          to: '/settings/security',
          icon: 'user',
        },
      ],
    },
  ];

  return (
    <div className="mesh-page">
      <SettingsLayout
        title={t('settings.title')}
        groups={groups}
        navLabel={t('settings.navLabel')}
      >
        <Outlet />
      </SettingsLayout>
    </div>
  );
}
