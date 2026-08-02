/**
 * MobileNav — 手机底部主导航(design-quality §4.3:0–599px MUST)。
 *
 * 五主入口:工作台 / 工作项 / 看板 / 聊天 + 「更多」抽屉触发;入口表与桌面侧栏
 * 同源(navigation.ts),图标 + 文字双通道(颜色非唯一信号,文本标签始终在场);
 * 当前页经 accent 态 + 3px 边缘指示表达(§4.1 同构);命中区 ≥44×44px(§8.2),
 * 底部适配 safe-area。全部文案经 i18n(mobileNav.*)。
 */
import { NavLink } from 'react-router';
import { Icon } from '../design';
import { useT } from '../i18n';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';
import { MOBILE_PRIMARY_KEYS, findNavItem, resolveNavTarget } from './navigation';
import type { NavItemDef } from './navigation';

export interface MobileNavProps {
  /** 「更多」触发回调(打开全高导航抽屉) */
  onOpenMore: () => void;
}

/**
 * 模块级解析入口表:MOBILE_PRIMARY_KEYS 是 navigation.ts 的静态字面量,键缺失即
 * 导航源配置错误,在导入时 fail-fast 抛错,而非渲染期静默兜底丢弃入口。
 */
const MOBILE_PRIMARY_ITEMS: ReadonlyArray<NavItemDef> = MOBILE_PRIMARY_KEYS.map((key) => {
  const item = findNavItem(key);
  if (item === undefined) {
    throw new Error('mobile nav entry missing: ' + key);
  }
  return item;
});

export function MobileNav(props: MobileNavProps): React.JSX.Element {
  const { onOpenMore } = props;
  const t = useT();
  const workspaceContext = useOptionalWorkspace();
  const workspace = workspaceContext !== null ? workspaceContext.workspace : null;
  return (
    <nav className="mesh-mobile-nav" aria-label={t('mobileNav.moreTitle')}>
      {MOBILE_PRIMARY_ITEMS.map((item) => (
        <NavLink
          key={item.key}
          to={resolveNavTarget(item, workspace?.slug ?? null)}
          end={item.end === true}
          data-testid={'mobile-nav-' + item.key}
          className={({ isActive }) =>
            isActive
              ? 'mesh-mobile-nav__item mesh-mobile-nav__item--active'
              : 'mesh-mobile-nav__item'
          }
        >
          <Icon name={item.icon} size={20} className="mesh-mobile-nav__icon" />
          <span className="mesh-mobile-nav__label">{t('mobileNav.' + item.key)}</span>
        </NavLink>
      ))}
      <button
        type="button"
        data-testid="mobile-nav-more"
        className="mesh-mobile-nav__item"
        onClick={onOpenMore}
      >
        <Icon name="menu" size={20} className="mesh-mobile-nav__icon" />
        <span className="mesh-mobile-nav__label">{t('mobileNav.more')}</span>
      </button>
    </nav>
  );
}
