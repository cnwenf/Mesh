/**
 * 徽标(design-quality.md §7.2):状态/优先级/类型标注。
 * 高度 20(sm)/24(md);内容为「图标 + 文案」,颜色不是唯一信号;
 * 文案禁换行(§6.4)。tone 对应状态三元组语义,不挪用为优先级/成员/图表色。
 */
import type { ReactNode } from 'react';
import { Badge as AppicaBadge } from '@appica/ui-react/badge';
import { Icon } from './Icon';
import type { IconName } from './Icon';
import './primitives.css';

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent';
export type BadgeSize = 'sm' | 'md';

/** 各 tone 的默认图标(状态可解释:图标形状 + 文案,不只靠颜色,§7.2)。 */
export const BADGE_TONE_ICONS: Readonly<Record<BadgeTone, IconName>> = Object.freeze({
  neutral: 'info',
  info: 'info',
  success: 'check',
  warning: 'warning',
  danger: 'error',
  accent: 'sparkle',
});

export interface BadgeProps {
  tone?: BadgeTone;
  size?: BadgeSize;
  /** 图标名;传 null 关闭图标;缺省用 tone 默认图标 */
  icon?: IconName | null;
  children: ReactNode;
  className?: string;
}

const APPICA_BADGE_VARIANT: Readonly<
  Record<BadgeTone, 'outline' | 'info' | 'success' | 'warning' | 'error' | 'primary'>
> = {
  neutral: 'outline',
  info: 'info',
  success: 'success',
  warning: 'warning',
  danger: 'error',
  accent: 'primary',
};

export function Badge(props: BadgeProps): React.JSX.Element {
  const { tone = 'neutral', size = 'sm', icon, children, className } = props;
  const iconName = icon === null ? null : (icon ?? BADGE_TONE_ICONS[tone]);
  const classes = [
    'mesh-badge',
    `mesh-badge--${tone}`,
    size === 'md' ? 'mesh-badge--md' : null,
    className,
  ]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  return (
    <AppicaBadge variant={APPICA_BADGE_VARIANT[tone]} size={size} className={classes}>
      {iconName !== null ? (
        <span className="mesh-badge__icon">
          <Icon name={iconName} size={16} />
        </span>
      ) : null}
      {children}
    </AppicaBadge>
  );
}
