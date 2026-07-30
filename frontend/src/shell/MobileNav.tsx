/**
 * MobileNav — 手机底部主导航(design-quality §4.3:0–599px MUST)。
 *
 * 五主入口:工作台 / 工作项 / 看板 / 聊天 + 「更多」抽屉触发;
 * 当前页经 NavLink aria-current=page 表达(颜色非唯一信号:文本标签始终在场);
 * 每项命中区 ≥44×44px(§8.2),底部适配 safe-area。全部文案经 i18n(mobileNav.*)。
 */
import { NavLink } from 'react-router';
import { useT } from '../i18n';

export interface MobileNavProps {
  /** 「更多」触发回调(打开全高导航抽屉) */
  onOpenMore: () => void;
}

interface MobileNavItem {
  readonly key: 'home' | 'issues' | 'board' | 'chat';
  readonly to: string;
}

const MOBILE_NAV_ITEMS: ReadonlyArray<MobileNavItem> = [
  { key: 'home', to: '/' },
  { key: 'issues', to: '/issues' },
  { key: 'board', to: '/board' },
  { key: 'chat', to: '/chat' },
];

export function MobileNav(props: MobileNavProps): React.JSX.Element {
  const { onOpenMore } = props;
  const t = useT();
  return (
    <nav className="mesh-mobile-nav" aria-label={t('mobileNav.moreTitle')}>
      {MOBILE_NAV_ITEMS.map((item) => (
        <NavLink
          key={item.key}
          to={item.to}
          end={item.to === '/'}
          data-testid={'mobile-nav-' + item.key}
          className={({ isActive }) =>
            isActive ? 'mesh-mobile-nav__item mesh-mobile-nav__item--active' : 'mesh-mobile-nav__item'
          }
        >
          {t('mobileNav.' + item.key)}
        </NavLink>
      ))}
      <button type="button" data-testid="mobile-nav-more" className="mesh-mobile-nav__item" onClick={onOpenMore}>
        {t('mobileNav.more')}
      </button>
    </nav>
  );
}
