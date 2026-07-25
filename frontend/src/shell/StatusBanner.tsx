/**
 * 连接状态横幅(README §6.12 异常态矩阵 offline/stale 行,§6.7 断线 UX)。
 * 纯展示组件:state 经 prop 注入,易于单测。
 *
 * - offline(无 token/未建连)呈现「网络已断开」横幅(§6.12 offline 行);
 * - reconnecting / resyncing 均呈现「正在重新同步…」横幅(§6.7:重连/重放过期时
 *   UI 显示「正在重新同步」),对账/重连成功后状态回 connected 无感消失;
 * - 其余状态(idle/connecting/connected)不渲染。
 * onRetry 为接口预留(§6.12 离线恢复入口为自动重连,本横幅不提供手动重试按钮)。
 */
import { Banner } from '../design';
import { useT } from '../i18n';
import type { ConnectionState } from '../realtime';

export interface StatusBannerProps {
  state: ConnectionState;
  /** 接口预留:离线恢复为自动重连,本组件当前不渲染手动重试控件 */
  onRetry?: () => void;
}

export function StatusBanner(props: StatusBannerProps): React.JSX.Element | null {
  const t = useT();
  const { state } = props;
  if (state === 'offline') {
    return (
      <Banner tone="warn" politeness="assertive">
        <span data-testid="status-banner-offline">{t('state.offline')}</span>
      </Banner>
    );
  }
  if (state === 'reconnecting' || state === 'resyncing') {
    return (
      <Banner tone="info">
        <span data-testid="status-banner-resyncing">{t('state.resyncing')}</span>
      </Banner>
    );
  }
  return null;
}
