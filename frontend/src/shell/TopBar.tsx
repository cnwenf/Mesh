/**
 * 顶栏(README §6.12):品牌、全局搜索(真实控件,§4.9)、连接状态点、命令面板/快捷键帮助入口。
 *
 * 搜索框为真实控件(value/onChange/onSubmit 接通):`/` 聚焦(等价鼠标路径:点击搜索框);
 * 输入非空即经 paletteBridge 携带查询展开命令面板同一结果视图(§4.9 键鼠一致);
 * 提交(Enter)同样展开。面板 open 态在 App 层(openPalette 无参),初始查询经
 * features/search/paletteBridge 模块级存储传递(不改动 App.tsx)。
 *
 * §6.12 硬约束:颜色不作唯一状态信号 —— StatusDot 的 label 文本始终在场。
 */
import { useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { IconButton, StatusDot } from '../design';
import type { StatusDotTone } from '../design';
import { InboxBell } from '../features/inbox';
import { setPaletteQuery } from '../features/search/paletteBridge';
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
  const [searchValue, setSearchValue] = useState('');

  /** 携带查询展开面板(§4.9):先写桥接查询再开面板,面板打开瞬间消费;随后清空本地值 */
  const openPaletteWithQuery = (value: string): void => {
    setPaletteQuery(value);
    onOpenPalette();
    setSearchValue('');
  };

  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const value = event.target.value;
    setSearchValue(value);
    if (value.trim() !== '') {
      openPaletteWithQuery(value);
    }
  };

  const handleSearchSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    openPaletteWithQuery(searchValue);
  };

  return (
    <header className="mesh-topbar" aria-label={t('a11y.topbar')}>
      <span className="mesh-topbar__brand">Mesh</span>
      <WorkspaceSwitcher />
      {/* display:contents:form 不生成盒子,输入框仍作为顶栏 flex 项保持既有布局 */}
      <form role="search" style={{ display: 'contents' }} onSubmit={handleSearchSubmit}>
        <input
          data-testid="topbar-search"
          className="mesh-topbar__search"
          type="search"
          placeholder={t('common.search')}
          aria-label={t('common.search')}
          value={searchValue}
          onChange={handleSearchChange}
        />
      </form>
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
