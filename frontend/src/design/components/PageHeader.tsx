/**
 * PageHeader(design-quality §1.2/§4.4):页面唯一主标题(title-1)+ 弱化描述 +
 * 动作区(原则上一个主操作)+ 可选 eyebrow(breadcrumb/上下文)。
 *
 * 各页面族(DataView/Detail/Settings)统一经本页头建立层级,禁止业务页复制外壳与
 * 页标题(§4.4 slot 扩展,不复制)。无硬编码文案。
 */
import type { ReactNode } from 'react';
import './components.css';

export interface PageHeaderProps {
  /** 页面主标题:渲染为唯一 h1 */
  title: string;
  /** 标题上方上下文槽(breadcrumb / 对象标识等,弱化色) */
  eyebrow?: ReactNode;
  /** 一句话描述(弱化色,body) */
  description?: string;
  /** 动作区(主/次按钮、筛选触发器等) */
  actions?: ReactNode;
  children?: ReactNode;
}

export function PageHeader(props: PageHeaderProps): React.JSX.Element {
  const { title, eyebrow, description, actions, children } = props;

  return (
    <header className="mesh-page-header">
      <div className="mesh-page-header__main">
        {eyebrow ? <div className="mesh-page-header__eyebrow">{eyebrow}</div> : null}
        <h1 className="mesh-page-header__title">{title}</h1>
        {description ? <p className="mesh-page-header__description">{description}</p> : null}
      </div>
      {actions ? <div className="mesh-page-header__actions">{actions}</div> : null}
      {children}
    </header>
  );
}
