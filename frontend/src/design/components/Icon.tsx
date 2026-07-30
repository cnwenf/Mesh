/**
 * 统一线性 SVG 图标(design-quality.md §7.1):
 * - 24×24 网格绘制,描边 1.75、圆角端点,颜色跟随 currentColor;
 * - 尺寸仅 16/20/24px 三档(空状态插画例外,不走本组件);
 * - 导航/按钮/状态/通知一律使用本图标集,禁 emoji 与字符图标;
 * - 默认 aria-hidden(伴随可见文案);独立使用时经 label 提供可访问名。
 */
import './primitives.css';

/** 图标名 → 路径数据(24×24 网格,多路径以空格分隔的独立 <path> 数组表达)。 */
export const ICON_PATHS: Readonly<Record<string, ReadonlyArray<string>>> = Object.freeze({
  'chevron-down': ['M6 9l6 6 6-6'],
  'chevron-up': ['M6 15l6-6 6 6'],
  'chevron-left': ['M15 6l-6 6 6 6'],
  'chevron-right': ['M9 6l6 6-6 6'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  plus: ['M12 5v14', 'M5 12h14'],
  search: ['M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14z', 'M21 21l-4.35-4.35'],
  check: ['M5 13l4 4L19 7'],
  info: ['M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z', 'M12 8h.01', 'M11 12h1v5h1'],
  warning: ['M12 3.5L2.5 20h19L12 3.5z', 'M12 10v4.5', 'M12 17.5h.01'],
  error: ['M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z', 'M9 9l6 6', 'M15 9l-6 6'],
  // AI 身份统一 glyph(§7.1:sparkle + 可见文字徽标,不依赖头像颜色)
  sparkle: ['M12 3l2 5.5L19.5 10 14 12l-2 5.5L10 12 4.5 10 10 8.5 12 3z', 'M19 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z'],
  user: ['M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8z', 'M4 20.5c0-3.9 3.6-6.5 8-6.5s8 2.6 8 6.5'],
  // agent 统一轮廓(§7.2:不用随机 emoji)
  agent: ['M5.5 8.5h13V19a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1V8.5z', 'M12 8.5V5', 'M12 5a1.25 1.25 0 1 0-.01 0z', 'M9.25 13.25h.01', 'M14.75 13.25h.01', 'M9 16.75h6'],
  'more-horizontal': ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'],
  inbox: ['M3 13.5h5l1.5 2.5h5l1.5-2.5h5', 'M5.5 5.5h13l3 8v5a1 1 0 0 1-1 1h-17a1 1 0 0 1-1-1v-5l3-8z'],
  settings: ['M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z', 'M12 2.5v3', 'M12 18.5v3', 'M4.5 6.8l2.6 1.5', 'M16.9 15.7l2.6 1.5', 'M2.5 12h3', 'M18.5 12h3', 'M4.5 17.2l2.6-1.5', 'M16.9 8.3l2.6-1.5'],
  home: ['M3 11l9-8 9 8', 'M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5'],
  board: ['M4 4.5h4.5v15H4z', 'M9.75 4.5h4.5v9.5h-4.5z', 'M15.5 4.5H20v12.5h-4.5z'],
  chat: ['M4 5.5h16V15H10.5L6 19v-4H4V5.5z'],
  folder: ['M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z'],
  bell: ['M6 9.5a6 6 0 1 1 12 0c0 4.5 1.8 5.8 1.8 5.8H4.2S6 14 6 9.5z', 'M10 19.5a2.2 2.2 0 0 0 4 0'],
  external: ['M18 13.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5.5', 'M14 4h6v6', 'M10 14L20 4'],
  refresh: ['M19.5 12a7.5 7.5 0 1 1-2.2-5.3', 'M19.5 4v4.5H15'],
  edit: ['M4 20l1.2-4.2L16.5 4.5a2.12 2.12 0 0 1 3 3L8.2 18.8 4 20z', 'M14.5 6.5l3 3'],
  trash: ['M4 7h16', 'M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2', 'M6.5 7l1 13a1 1 0 0 0 1 .9h7a1 1 0 0 0 1-.9l1-13', 'M10 11.5v5.5', 'M14 11.5v5.5'],
  send: ['M21.5 2.5L11 13', 'M21.5 2.5l-6.8 19-3.7-8.5-8.5-3.7 19-6.8z'],
  calendar: ['M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6z', 'M4 10.5h16', 'M8.5 2.5v3', 'M15.5 2.5v3'],
  filter: ['M4 5h16l-6 7v5.5l-4 2V12L4 5z'],
  grip: ['M9 6h.01', 'M15 6h.01', 'M9 12h.01', 'M15 12h.01', 'M9 18h.01', 'M15 18h.01'],
  logout: ['M9 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3', 'M16 17l5-5-5-5', 'M21 12H9'],
});

export type IconName = keyof typeof ICON_PATHS;

/** 允许的图标尺寸(design-quality.md §7.1)。 */
export type IconSize = 16 | 20 | 24;

export interface IconProps {
  name: IconName;
  /** 尺寸(px),仅 16/20/24,默认 20 */
  size?: IconSize;
  /** 独立使用时的可访问名;伴随可见文案时留空(图标对读屏隐藏) */
  label?: string;
  className?: string;
}

export function Icon(props: IconProps): React.JSX.Element {
  const { name, size = 20, label, className } = props;
  const paths = ICON_PATHS[name];
  const classes = ['mesh-icon', `mesh-icon--${size}`, className]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  return (
    <svg
      className={classes}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      role={label !== undefined ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label === undefined ? true : undefined}
      focusable="false"
    >
      {paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
