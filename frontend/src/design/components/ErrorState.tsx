/**
 * 错误态(design-quality.md §7.7 四部分):
 * 1. 发生了什么(title/description);
 * 2. 哪部分受影响、输入是否保留(impact);
 * 3. 可执行恢复动作(onRetry + retryLabel,或 action 插槽);
 * 4. 可复制诊断 ID(diagnosticId,user-select:all 一键复制)。
 * 禁止仅显示「出错了」「请求失败」或裸错误码——调用方必须给出影响与恢复路径。
 * 重试按钮仅在 onRetry 与 retryLabel 同时提供时渲染(避免无标签控件)。无硬编码文案。
 */
import type { ReactNode } from 'react';
import { Button } from './Button';
import './components.css';

export interface ErrorStateProps {
  title: string;
  description?: string;
  /** 影响说明:哪部分受影响、已有输入/数据是否保留(§7.7 第 2 部分) */
  impact?: string;
  /** 重试回调 */
  onRetry?: () => void;
  /** 重试按钮文案(来自调用方,配合 onRetry 使用) */
  retryLabel?: string;
  /** 自定义恢复动作插槽(优先于 onRetry;如「返回上一页」) */
  action?: ReactNode;
  /** 诊断 ID(服务端提供时),渲染为可复制等宽块(§7.7 第 4 部分) */
  diagnosticId?: string;
  /** 帮助链接插槽(可选) */
  help?: ReactNode;
  /** 插画插槽 */
  illustration?: ReactNode;
}

export function ErrorState(props: ErrorStateProps): React.JSX.Element {
  const { title, description, impact, onRetry, retryLabel, action, diagnosticId, help, illustration } = props;
  const showRetry = onRetry !== undefined && retryLabel !== undefined;
  return (
    <div className="mesh-error-state">
      {illustration ? <div className="mesh-error-state__illustration">{illustration}</div> : null}
      <p className="mesh-error-state__title">{title}</p>
      {description ? <p className="mesh-error-state__description">{description}</p> : null}
      {impact ? <p className="mesh-error-state__impact">{impact}</p> : null}
      {action ?? (showRetry ? (
        <div className="mesh-error-state__action">
          <Button variant="secondary" onClick={onRetry}>
            {retryLabel}
          </Button>
        </div>
      ) : null)}
      {diagnosticId !== undefined && diagnosticId.length > 0 ? (
        <code className="mesh-error-state__diagnostic">{diagnosticId}</code>
      ) : null}
      {help ? <div className="mesh-error-state__help">{help}</div> : null}
    </div>
  );
}
