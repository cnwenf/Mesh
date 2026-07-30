/**
 * PublicFlowShell — 公共流程统一外壳(design-quality.md §4.4 PublicFlow 模板)。
 *
 * 登录/注册/MFA/找回/重置、设备授权、邀请接受、OAuth 回调等公共页共用本外壳:
 *   品牌区(可返回首页)+ 单任务卡(标题/说明/children)+ 安全·帮助信息(footer)。
 * 解决「公共页之间缺少一致外壳」(§3.2 设备授权/邀请/OAuth 行)。
 *
 * 约定:
 * - 全部可见文案经 prop 注入,组件内无硬编码字符串(与设计系统其余组件一致);
 * - 视觉一律经语义 token 与排版工具类(.mesh-text-title-1),禁硬编码色值;
 * - 品牌 mark 用 currentColor,随亮/暗主题自适应;
 * - `brandHref` 提供时品牌为可返回首页的链接(§4.2),否则呈现已读品牌(非交互)。
 */
import type { ReactNode } from 'react';
import './publicFlow.css';

export interface PublicFlowShellProps {
  /** 品牌名(品牌区文字) */
  brandLabel: string;
  /** 品牌返回首页链接;提供时渲染 <a>,否则渲染非交互品牌 */
  brandHref?: string;
  /** 单任务卡主标题(渲染为唯一 h1,§10.2) */
  title: string;
  /** 标题下的说明文案(可选) */
  description?: string;
  /** 单任务卡主体(表单/状态/结果) */
  children: ReactNode;
  /** 卡下安全·帮助信息区(可选) */
  footer?: ReactNode;
}

/** 品牌 mark:网状节点图形,currentColor 随主题自适应(无硬编码色)。 */
function BrandMark(): React.JSX.Element {
  return (
    <svg
      className="mesh-public-flow__mark"
      viewBox="0 0 24 24"
      width="24"
      height="24"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M4 18V6l8 6 8-6v12"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="4" cy="6" r="1.6" fill="currentColor" />
      <circle cx="20" cy="6" r="1.6" fill="currentColor" />
      <circle cx="12" cy="12" r="1.6" fill="currentColor" />
      <circle cx="4" cy="18" r="1.6" fill="currentColor" />
      <circle cx="20" cy="18" r="1.6" fill="currentColor" />
    </svg>
  );
}

export function PublicFlowShell(props: PublicFlowShellProps): React.JSX.Element {
  const { brandLabel, brandHref, title, description, children, footer } = props;
  return (
    <div className="mesh-public-flow">
      <div className="mesh-public-flow__frame">
        <header className="mesh-public-flow__brand">
          {brandHref !== undefined ? (
            <a className="mesh-public-flow__brand-link" href={brandHref}>
              <BrandMark />
              <span className="mesh-public-flow__brand-name">{brandLabel}</span>
            </a>
          ) : (
            <span className="mesh-public-flow__brand-link" aria-hidden="false">
              <BrandMark />
              <span className="mesh-public-flow__brand-name">{brandLabel}</span>
            </span>
          )}
        </header>

        <main className="mesh-public-flow__card">
          <h1 className="mesh-public-flow__title mesh-text-title-1">{title}</h1>
          {description !== undefined && description.length > 0 ? (
            <p className="mesh-public-flow__description">{description}</p>
          ) : null}
          {children}
        </main>

        {footer !== undefined ? (
          <footer className="mesh-public-flow__footer">{footer}</footer>
        ) : null}
      </div>
    </div>
  );
}
