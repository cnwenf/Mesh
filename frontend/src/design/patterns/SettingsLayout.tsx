/**
 * SettingsLayout — 设置页模板(design-quality.md §4.4 Settings:二级导航 + 内容列 + dirty/save 区;
 * §11.1 patterns 层,API 稳定)。
 *
 * - 桌面(>599px):左侧 sticky 二级导航;当前项 = 浅强调底 + 强文字 + 字重 + 3px 边缘指示
 *   (§4.1:颜色不作唯一信号,叠加字重与形状);按组渲染组标题。
 * - 手机(≤599px,§8.1/§8.3):导航折叠为内容上方的分组列表(纯 CSS,非 <select>)。
 * - `hidden` 项跳过渲染(权限不可见,§3.2,而非禁用);全隐藏的组不渲染。
 * - 内容列最大宽 `var(--content-form, 640px)`。
 * - 断点复用外壳集中值,经 patterns.css 统一控制,组件不读窗口宽度。
 */
import type { ReactNode } from 'react';
import { NavLink } from 'react-router';
import { Icon } from '../components/Icon';
import type { IconName } from '../components/Icon';
import './patterns.css';

export interface SettingsNavItem {
  /** 稳定键(列表 key) */
  key: string;
  /** 导航项可见文案(来自调用方 t()) */
  label: string;
  /** 目标路由 */
  to: string;
  /** NavLink end 匹配(索引/精确路由用) */
  end?: boolean;
  /** 可选图标 */
  icon?: IconName;
  /** 权限不可见:跳过渲染(§3.2,非禁用) */
  hidden?: boolean;
}

export interface SettingsNavGroup {
  /** 组标题(可选) */
  label?: string;
  items: ReadonlyArray<SettingsNavItem>;
}

export interface SettingsLayoutProps {
  /** 页面标题(h1) */
  title: string;
  /** 页面副标题(可选) */
  description?: string;
  /** 二级导航分组 */
  groups: ReadonlyArray<SettingsNavGroup>;
  /** 内容列(通常为 <Outlet />) */
  children: ReactNode;
  /** 导航区可访问名(aria-label) */
  navLabel: string;
}

/** 过滤隐藏项;返回可见项列表(不可变)。 */
function visibleItems(group: SettingsNavGroup): ReadonlyArray<SettingsNavItem> {
  return group.items.filter((item) => item.hidden !== true);
}

function NavItemLink({ item }: { item: SettingsNavItem }): React.JSX.Element {
  return (
    <li className="mesh-settings-layout__item">
      <NavLink
        to={item.to}
        end={item.end}
        data-testid={`settings-nav-${item.key}`}
        className={({ isActive }) =>
          isActive ? 'mesh-settings-layout__link is-active' : 'mesh-settings-layout__link'
        }
      >
        {item.icon !== undefined ? (
          <Icon name={item.icon} size={20} className="mesh-settings-layout__link-icon" />
        ) : null}
        <span className="mesh-settings-layout__link-label">{item.label}</span>
      </NavLink>
    </li>
  );
}

export function SettingsLayout(props: SettingsLayoutProps): React.JSX.Element {
  const { title, description, groups, children, navLabel } = props;
  const visibleGroups = groups
    .map((group) => ({ label: group.label, items: visibleItems(group) }))
    .filter((group) => group.items.length > 0);

  return (
    <div className="mesh-settings-layout">
      <nav className="mesh-settings-layout__nav" aria-label={navLabel}>
        <div className="mesh-settings-layout__header">
          <h1 className="mesh-settings-layout__title">{title}</h1>
          {description !== undefined ? (
            <p className="mesh-settings-layout__description">{description}</p>
          ) : null}
        </div>
        {visibleGroups.map((group, index) => (
          <div className="mesh-settings-layout__group" key={group.label ?? `group-${index}`}>
            {group.label !== undefined ? (
              <p className="mesh-settings-layout__group-label">{group.label}</p>
            ) : null}
            <ul className="mesh-settings-layout__list">
              {group.items.map((item) => (
                <NavItemLink key={item.key} item={item} />
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <div className="mesh-settings-layout__content">{children}</div>
    </div>
  );
}
