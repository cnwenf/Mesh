/**
 * 顶栏(README §6.12):品牌、全局搜索(/ 聚焦目标)、连接状态点、命令面板/快捷键帮助入口。
 * 纯展示:连接 state 与打开回调均经 prop 注入。
 * §6.12 硬约束:颜色不作唯一状态信号 —— StatusDot 的 label 文本始终在场。
 */
import { IconButton, StatusDot } from '../design';
import type { StatusDotTone } from '../design';
import { InboxBell } from '../features/inbox';
import { useT } from '../i18n';
import type { ConnectionState } from '../realtime';
import { WorkspaceSwitcher } from '../workspace/WorkspaceSwitcher';

export interface TopBarProps {
  state: ConnectionState;
  onOpenPalette: () => void;
  onOpenHelp: () => void;
}

const TONE_BY_STATE: Record<ConnectionState, StatusDotTone> = {
  connected: 'success',
  connecting: 'warn',
  reconnecting: 'warn',
  resyncing: 'info',
  offline: 'danger',
  idle: 'neutral',
};

/** 进行中状态叠加脉冲(文本信号始终存在,pulse 仅为视觉提示) */
const PULSING_STATES: ReadonlySet<ConnectionState> = new Set<ConnectionState>([
  'connecting',
  'reconnecting',
  'resyncing',
]);

export function TopBar(props: TopBarProps): React.JSX.Element {
  const t = useT();
  const { state, onOpenPalette, onOpenHelp } = props;
  return (
    <header className="mesh-topbar" aria-label={t('a11y.topbar')}>
      <span className="mesh-topbar__brand">Mesh</span>
      <WorkspaceSwitcher />
      <input
        data-testid="topbar-search"
        className="mesh-topbar__search"
        type="search"
        placeholder={t('common.search')}
        aria-label={t('common.search')}
      />
      <span className="mesh-topbar__conn" data-testid="conn-status">
        <StatusDot tone={TONE_BY_STATE[state]} label={t('status.' + state)} pulse={PULSING_STATES.has(state)} />
      </span>
      <span className="mesh-topbar__actions">
        <InboxBell />
        <IconButton data-testid="open-palette" label={t('a11y.openPalette')} onClick={onOpenPalette}>
          ⌘
        </IconButton>
        <IconButton data-testid="open-help" label={t('a11y.openHelp')} onClick={onOpenHelp}>
          ?
        </IconButton>
      </span>
    </header>
  );
}
