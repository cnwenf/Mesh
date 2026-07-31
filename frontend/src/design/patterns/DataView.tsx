/**
 * 页面模板 · DataView(design-quality.md §4.4/§3.2 Issue 列表行)。
 *
 * 结构:PageHeader(唯一 h1 + 动作槽)+ 工具条槽(保存视图/过滤 chips/排序)+
 * 内容区(表格/行列表,经 container query 自适应;手机转主次行由行 CSS 承担)+
 * 分页槽 + 粘底批量条。业务页只填槽,不复制页头与状态组件(§4.4 末段)。
 *
 * API 面向批次③④复用(成员/收件箱/runtime/自动值守列表)保持稳定:只加槽,
 * 不改既有签名(MES-125 协同约束)。
 */
import type { ReactNode } from 'react';
import { PageHeader } from '../components/PageHeader';

/** 面包屑项(末项无 to,经 aria-current 标注)。 */
export interface PageHeaderCrumb {
  readonly label: string;
  readonly to?: string;
}
import './patterns.css';

export interface DataViewProps {
  /** 页面标题(唯一 h1) */
  readonly title: string;
  readonly crumbs?: readonly PageHeaderCrumb[];
  readonly breadcrumbLabel?: string;
  readonly description?: string;
  /** 页面级动作槽(一个主 CTA,§13.1) */
  readonly actions?: ReactNode;
  /** 视图/筛选条槽:保存视图切换、FilterChips、排序与密度控件 */
  readonly toolbar?: ReactNode;
  /** 列表/表格主体(行容器应使用 container query 做主次行降级) */
  readonly children: ReactNode;
  /** 分页/加载更多槽 */
  readonly footer?: ReactNode;
  /** 粘底批量条(BulkBar);仅在传入且选中 ≥1 时渲染 */
  readonly bulkBar?: ReactNode;
  readonly className?: string;
}

export function DataView(props: DataViewProps): React.JSX.Element {
  const { title, crumbs, breadcrumbLabel, description, actions, toolbar, children, footer, bulkBar, className } =
    props;
  const classes = ['mesh-data-view', className].filter(Boolean).join(' ');
  return (
    <div className={classes} data-testid="data-view">
      <PageHeader
        title={title}
        eyebrow={
          crumbs !== undefined && crumbs.length > 0 ? (
            <nav className="mesh-data-view__crumbs" aria-label={breadcrumbLabel ?? 'breadcrumb'}>
              <ol>
                {crumbs.map((crumb, index) => (
                  <li key={`${crumb.label}-${index}`}>
                    {crumb.to !== undefined ? (
                      <a href={crumb.to}>{crumb.label}</a>
                    ) : (
                      <span aria-current="page">{crumb.label}</span>
                    )}
                  </li>
                ))}
              </ol>
            </nav>
          ) : undefined
        }
        description={description}
        actions={actions}
      />
      {toolbar !== undefined ? <div className="mesh-data-view__toolbar">{toolbar}</div> : null}
      <div className="mesh-data-view__body">{children}</div>
      {footer !== undefined ? <div className="mesh-data-view__footer">{footer}</div> : null}
      {bulkBar}
    </div>
  );
}
