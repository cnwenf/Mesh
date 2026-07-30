/**
 * 顶栏(README §6.12):品牌、全局搜索(真实控件,§4.9)、连接状态点、命令面板/快捷键帮助入口。
 * 纯展示:连接 state 与打开回调均经 prop 注入。
 * §6.12 硬约束:颜色不作唯一状态信号 —— StatusDot 的 label 文本始终在场。
 * §4.2 顶栏:品牌为返回首页链接;连接状态在稳定态(connected/idle)仅呈现状态点 +
 * tooltip 可读名,连接中/重连/重同步/离线四个进行/异常态才显式呈现文本标签。
 *
 * 搜索框即统一搜索入口(design-quality A-02 / search-command-palette.md S1):
 * 键入首字符或回车即经 onOpenSearch 携带查询展开命令面板同一结果视图(§4.9 键鼠一致),
 * 随后焦点交接给面板搜索框;不允许存在无行为输入框。Esc 仅清空本框。
 */
import { useState } from 'react';
import { NavLink } from 'react-router';
import type { ChangeEvent, KeyboardEvent } from 'react';
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
  /** 统一搜索入口:携带查询展开命令面板(见文件头注释) */
  onOpenSearch: (query: string) => void;
}

const TONE_BY_STATE: Record<ConnectionState, StatusDotTone> = {
  connected: 'success',
  connecting: 'warn',
  reconnecting: 'warn',
  resyncing: 'info',
  offline: 'danger',
  idle: 'neutral',
};

/** 进行中状态叠加脉冲(文本信号在场时 pulse 仅为视觉提示) */
const PULSING_STATES: ReadonlySet<ConnectionState> = new Set<ConnectionState>([
  'connecting',
  'reconnecting',
  'resyncing',
]);

/**
 * 文本呈现态(§4.2):连接中/重连/重同步/离线——用户需要知道正在进行或出了问题;
 * 稳定态(connected/idle)仅状态点 + tooltip,减少常态噪音。
 */
const TEXT_VISIBLE_STATES: ReadonlySet<ConnectionState> = new Set<ConnectionState>([
  'connecting',
  'reconnecting',
  'resyncing',
  'offline',
]);

export function TopBar(props: TopBarProps): React.JSX.Element {
  const t = useT();
  const { state, onOpenPalette, onOpenHelp, onOpenSearch } = props;
  const [searchValue, setSearchValue] = useState('');

  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const value = event.target.value;
    if (value.length === 0) {
      setSearchValue('');
      return;
    }
    // 续输入即展开统一搜索面板并交接(焦点将移入面板搜索框),本框清空。
    setSearchValue('');
    onOpenSearch(value);
  };

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'Enter') {
      event.preventDefault();
      const query = searchValue;
      setSearchValue('');
      onOpenSearch(query);
    } else if (event.key === 'Escape') {
      setSearchValue('');
    }
  };

  return (
    <header className="mesh-topbar" aria-label={t('a11y.topbar')}>
      {/* §4.2:品牌是返回首页的链接 */}
      <NavLink to="/" data-testid="topbar-brand" className="mesh-topbar__brand">
        Mesh
      </NavLink>
      <WorkspaceSwitcher />
      <input
        data-testid="topbar-search"
        className="mesh-topbar__search"
        type="search"
        placeholder={t('common.search')}
        aria-label={t('common.search')}
        value={searchValue}
        onChange={handleSearchChange}
        onKeyDown={handleSearchKeyDown}
      />
      <span className="mesh-topbar__conn" data-testid="conn-status">
        {TEXT_VISIBLE_STATES.has(state) ? (
          <StatusDot tone={TONE_BY_STATE[state]} label={t('status.' + state)} pulse={PULSING_STATES.has(state)} />
        ) : (
          // 稳定态:仅状态点(§4.2)。可读名经 aria-label(读屏)+ title(悬停提示)
          // 承载,颜色非唯一信号;不用 Tooltip 组件——其内联浮层在视口右缘会撑出
          // 页面级横向滚动(320px 溢出门禁),title 零布局副作用。
          <span
            className="mesh-status"
            role="img"
            aria-label={t('status.' + state)}
            title={t('status.' + state)}
          >
            <span
              className={'mesh-status__dot mesh-status__dot--' + TONE_BY_STATE[state]}
              aria-hidden="true"
            />
          </span>
        )}
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
