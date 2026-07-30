/**
 * 页面模板 · Detail(design-quality.md §4.4 Detail 行 / §8.3 Issue 详情重排)。
 *
 * 桌面(wide container):主内容 + 320px 属性侧栏两栏,侧栏 sticky。
 * 窄容器(<900px)与手机:侧栏收起为「属性」按钮,打开底部 Drawer(设计层
 * Drawer 在 ≤599px 自动转底部 sheet,§8.3「属性栏变属性按钮,打开底部 sheet」)。
 * 关键状态/负责人经 summaryChips 槽保留在标题下(§8.3)。
 *
 * 布局纯 CSS container query 决定(§11.2:不用 JS 读窗口宽度做视觉布局);
 * 抽屉开关是交互状态,不在此列。
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Drawer } from '../components/Drawer';
import './patterns.css';

export interface DetailLayoutProps {
  /** 对象头:breadcrumb + identifier + 内联标题编辑 + 动作 */
  readonly header: ReactNode;
  /** 标题下的关键状态 chips(桌面/手机均可见,§8.3) */
  readonly summaryChips?: ReactNode;
  /** 主内容(描述/子项/讨论等) */
  readonly main: ReactNode;
  /** 属性侧栏内容;省略时不渲染侧栏与属性按钮 */
  readonly aside?: ReactNode;
  /** 侧栏/抽屉标题与触发按钮文案(调用方按 locale 传入) */
  readonly asideTitle?: string;
  readonly asideTriggerLabel?: string;
  /** Drawer 关闭按钮可访问名 */
  readonly closeLabel?: string;
  readonly className?: string;
}

export function DetailLayout(props: DetailLayoutProps): React.JSX.Element {
  const { header, summaryChips, main, aside, asideTitle, asideTriggerLabel, closeLabel, className } =
    props;
  const [asideOpen, setAsideOpen] = useState(false);
  const classes = ['mesh-detail-layout', className].filter(Boolean).join(' ');
  const hasAside = aside !== undefined;
  return (
    <div className={classes} data-testid="detail-layout">
      <div className="mesh-detail-layout__header">{header}</div>
      {summaryChips !== undefined ? (
        <div className="mesh-detail-layout__chips" data-testid="detail-summary-chips">
          {summaryChips}
        </div>
      ) : null}
      {hasAside ? (
        <button
          type="button"
          className="mesh-detail-layout__aside-trigger"
          aria-expanded={asideOpen}
          data-testid="detail-aside-trigger"
          onClick={() => setAsideOpen(true)}
        >
          {asideTriggerLabel ?? asideTitle ?? ''}
        </button>
      ) : null}
      <div className="mesh-detail-layout__columns">
        <div className="mesh-detail-layout__main">{main}</div>
        {hasAside ? (
          <aside className="mesh-detail-layout__aside" aria-label={asideTitle} data-testid="detail-aside">
            {aside}
          </aside>
        ) : null}
      </div>
      {hasAside ? (
        <Drawer
          open={asideOpen}
          onClose={() => setAsideOpen(false)}
          title={asideTitle ?? ''}
          closeLabel={closeLabel}
        >
          <div className="mesh-detail-layout__aside-sheet" data-testid="detail-aside-sheet">
            {aside}
          </div>
        </Drawer>
      ) : null}
    </div>
  );
}
