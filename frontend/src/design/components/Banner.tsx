/**
 * 横幅:tone(info/warn/danger/success)+ politeness(polite→role=status / assertive→role=alert,
 * 均显式 aria-live)。可选关闭按钮(onDismiss + dismissLabel 同时提供时渲染)。无硬编码文案。
 */
import type { ReactNode } from 'react';
import { IconButton } from './IconButton';
import './components.css';

export type BannerTone = 'info' | 'warn' | 'danger' | 'success';

export interface BannerProps {
  tone: BannerTone;
  children: ReactNode;
  /** live region 礼貌级别,默认 polite */
  politeness?: 'polite' | 'assertive';
  onDismiss?: () => void;
  /** 关闭按钮可访问名(来自调用方,配合 onDismiss 使用) */
  dismissLabel?: string;
}

export function Banner(props: BannerProps): React.JSX.Element {
  const { tone, children, politeness = 'polite', onDismiss, dismissLabel } = props;
  const isAlert = politeness === 'assertive';
  const showDismiss = onDismiss !== undefined && dismissLabel !== undefined;
  return (
    <div
      className={`mesh-banner mesh-banner--${tone}`}
      role={isAlert ? 'alert' : 'status'}
      aria-live={isAlert ? 'assertive' : 'polite'}
    >
      <div className="mesh-banner__content">{children}</div>
      {showDismiss ? (
        <IconButton label={dismissLabel} onClick={onDismiss} className="mesh-banner__dismiss">
          ×
        </IconButton>
      ) : null}
    </div>
  );
}
