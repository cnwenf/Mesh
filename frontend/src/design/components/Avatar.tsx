/**
 * 头像(design-quality.md §7.2):
 * - 尺寸 20/24/32/40/56px;
 * - 人类无图时姓名缩写(稳定 hash 取色 h0–h7,亮/暗表面均可读,见 tokenValues 头像组);
 * - agent 统一轮廓(sparkle glyph),不用随机 emoji;
 * - 有图时渲染图片(alt 留空:头像旁的名称文案承载可访问名)。
 */
import { Icon } from './Icon';
import './primitives.css';

export type AvatarSize = 20 | 24 | 32 | 40 | 56;

export interface AvatarProps {
  /** 显示名(缩写与 hash 取色的来源;agent 缺省用 'Agent') */
  name: string;
  size?: AvatarSize;
  /** 身份类型:human 姓名缩写,agent 统一轮廓 */
  kind?: 'human' | 'agent';
  /** 头像图片 URL(有值优先) */
  src?: string;
  className?: string;
}

/** djb2 哈希:同一身份跨会话稳定取色(§7.2 稳定 hash)。 */
export function avatarHueIndex(name: string): number {
  let hash = 5381;
  for (let i = 0; i < name.length; i += 1) {
    hash = ((hash << 5) + hash + name.codePointAt(i)!) >>> 0;
  }
  return hash % 8;
}

/**
 * 姓名缩写:拉丁名取前两个词首字母(大写);CJK 名取末两字;
 * 单字名取首字。空名回退 '?'。
 */
export function avatarInitials(name: string): string {
  const trimmed = name.trim();
  if (trimmed.length === 0) return '?';
  const words = trimmed.split(/\s+/).filter((word) => word.length > 0);
  if (words.length >= 2) {
    return `${words[0].charAt(0)}${words[1].charAt(0)}`.toUpperCase();
  }
  const single = words[0];
  const chars = Array.from(single);
  // CJK 单名取末两字(中文称呼习惯);拉丁单名取首字母大写
  const first = chars[0].codePointAt(0)!;
  const isCJK = first >= 0x2e80;
  if (isCJK && chars.length >= 2) {
    return chars.slice(-2).join('');
  }
  return single.charAt(0).toUpperCase();
}

export function Avatar(props: AvatarProps): React.JSX.Element {
  const { name, size = 32, kind = 'human', src, className } = props;
  const baseClasses = ['mesh-avatar', `mesh-avatar--${size}`, className]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  if (src !== undefined && src.length > 0) {
    return (
      <span className={baseClasses}>
        <img src={src} alt="" />
      </span>
    );
  }

  if (kind === 'agent') {
    const classes = [baseClasses, 'mesh-avatar--agent'].join(' ');
    return (
      <span className={classes} role="img" aria-label={name}>
        <Icon name="agent" size={size >= 40 ? 24 : 16} />
      </span>
    );
  }

  const hue = avatarHueIndex(name);
  const classes = [baseClasses, `mesh-avatar--h${hue}`].join(' ');
  return (
    <span className={classes} role="img" aria-label={name}>
      {avatarInitials(name)}
    </span>
  );
}
