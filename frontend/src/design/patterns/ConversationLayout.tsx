/**
 * ConversationLayout — 「列表/详情双栏;手机单栏路由化」页面模板
 * (design-quality.md §4.4 Conversation 模板、§3.2 收件箱行/聊天行、§11.1 patterns 层)。
 *
 * 收件箱(分组列表 + 预览)与聊天(会话列表 + 会话)共用本模式;与批次②
 * DataView/DetailLayout 及批次④ SettingsLayout 同层共存,API 稳定:
 *
 * - medium 以上(容器 ≥600px):grid 双栏,左栏 `minmax(16rem, 20rem)` 列表,右栏详情。
 * - compact(容器 ≤599px,§8.1/§8.3):单栏,经 `activePane` 决定可见窗格——路由化由调用方承担
 *   (如 /chat 与 /chat/:sessionId、/inbox 与 /inbox/:notificationId),本组件不读路由、
 *   不读窗口宽度,纯由 prop 驱动；布局由自身外层 container query 决定。
 *
 * 颜色一律经语义 token;两栏为语义 <section> 并带可访问名(aria-label)。
 */
import type { ReactNode } from 'react';
import './ConversationLayout.css';

export type ConversationPane = 'list' | 'detail';

export interface ConversationLayoutProps {
  /** 列表窗格内容(桌面左栏 / 手机 activePane='list' 时可见) */
  list: ReactNode;
  /** 列表窗格可访问名(aria-label) */
  listLabel: string;
  /** 详情窗格内容(桌面右栏 / 手机 activePane='detail' 时可见) */
  children: ReactNode;
  /** 详情窗格可访问名(可选 aria-label) */
  detailLabel?: string;
  /** 手机单栏下哪个窗格可见(桌面恒双栏);缺省 'list' */
  activePane?: ConversationPane;
  className?: string;
}

export function ConversationLayout(props: ConversationLayoutProps): React.JSX.Element {
  const { list, listLabel, children, detailLabel, activePane = 'list', className } = props;
  const classes = ['mesh-conversation-layout', className]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  return (
    <div className="mesh-conversation-layout-container">
      <div className={classes} data-active-pane={activePane}>
        <section className="mesh-conversation-layout__list" aria-label={listLabel}>
          {list}
        </section>
        <section className="mesh-conversation-layout__detail" aria-label={detailLabel}>
          {children}
        </section>
      </div>
    </div>
  );
}
