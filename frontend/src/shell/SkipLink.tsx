/**
 * 跳到主内容链接(design-quality §10.2:「跳到主内容」链接与稳定 main 锚点)。
 *
 * 键盘用户首焦即达,Enter 后焦点落主区(main 需 tabIndex=-1 与对应 id);
 * 视觉上默认移出视口,聚焦时滑入(样式见 shell.css .mesh-skip-link)。
 * 无硬编码可见文案(label 由调用方经 i18n 提供)。
 */
import { MAIN_CONTENT_ID } from '../a11y';

export { MAIN_CONTENT_ID } from '../a11y';

export interface SkipLinkProps {
  /** 链接可见文案(如「跳到主内容」) */
  label: string;
}

export function SkipLink(props: SkipLinkProps): React.JSX.Element {
  const { label } = props;
  return (
    <a className="mesh-skip-link" href={`#${MAIN_CONTENT_ID}`}>
      {label}
    </a>
  );
}
