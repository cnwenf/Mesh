/**
 * 中性骨架屏(theme.md §2.3 ③):协商完成前的最后兜底。
 *
 * 与主题无关的中性灰阶(经 --color-skeleton-* token,亮/暗同族),不呈现
 * 业务内容——「宁可短暂无主题骨架,不可先错后改」。
 */
import './skeleton.css';

export function ThemeSkeleton(): React.JSX.Element {
  return (
    <div className="mesh-theme-skeleton" data-testid="theme-skeleton" aria-busy="true">
      <div className="mesh-theme-skeleton__sidebar" aria-hidden="true">
        <div className="mesh-theme-skeleton__bar mesh-theme-skeleton__bar--logo" />
        <div className="mesh-theme-skeleton__bar" />
        <div className="mesh-theme-skeleton__bar" />
        <div className="mesh-theme-skeleton__bar mesh-theme-skeleton__bar--short" />
      </div>
      <div className="mesh-theme-skeleton__main" aria-hidden="true">
        <div className="mesh-theme-skeleton__bar mesh-theme-skeleton__bar--title" />
        <div className="mesh-theme-skeleton__grid">
          <div className="mesh-theme-skeleton__block" />
          <div className="mesh-theme-skeleton__block" />
          <div className="mesh-theme-skeleton__block" />
          <div className="mesh-theme-skeleton__block" />
        </div>
      </div>
    </div>
  );
}
