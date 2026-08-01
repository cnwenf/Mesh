/**
 * 顶栏(README §6.12):品牌、全局搜索(/ 聚焦目标)、连接状态点、命令面板/快捷键帮助入口。
 * 纯展示 + 搜索编排:连接 state 与打开回调均经 prop 注入。
 * §6.12 硬约束:颜色不作唯一状态信号——进行/异常态有文本标签,稳定态的
 * 可读名经 aria-label + title 承载(读屏/悬停双通道)。
 * §4.2 顶栏:品牌为返回首页链接;连接状态在稳定态(connected/idle)仅呈现状态点 +
 * tooltip 可读名,连接中/重连/重同步/离线四个进行/异常态才显式呈现文本标签。
 *
 * 搜索框为真实控件(search-command-palette.md §4.9 / design-quality §9.6):
 * - 产品接线使用 `palette` 模式:键入首字符即携查询交接完整命令面板;
 * - `inline` 模式保留输入框下方结果弹层,渲染与命令面板**同一结果组件**
 *   (PaletteResults + usePaletteData,同一 hook 同一数据源),供嵌入场景复用;
 * - Enter(无选中项)携带查询展开完整命令面板(onOpenSearch 统一入口);
 *   有选中项 → 直接激活该项(与面板键盘语义一致);
 * - ↑↓ 在弹层内移动选择、Enter 激活、Esc 关闭弹层(再按清空输入);
 * - 无工作区上下文时弹层仅呈现本地命令(workspaceId null → 不请求,§3.2)。
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { NavLink } from 'react-router';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { useNavigate } from 'react-router';
import { IconButton, StatusDot } from '../design';
import type { StatusDotTone } from '../design';
import { InboxBell } from '../features/inbox';
import { useT } from '../i18n';
import type { ConnectionState } from '../realtime';
import { PaletteResults } from '../shortcuts/PaletteResults';
import { activatePaletteOption, flattenSections, moveSelection } from '../shortcuts/paletteModel';
import type { PaletteOption } from '../shortcuts/paletteModel';
import { pushRecent, trackCommandUse } from '../shortcuts/recents';
import { usePaletteContext } from '../shortcuts/usePaletteContext';
import { usePaletteData } from '../shortcuts/usePaletteData';
import type { FavoritesProvider } from '../shortcuts/usePaletteData';
import { WorkspaceSwitcher } from '../workspace/WorkspaceSwitcher';

export interface TopBarProps {
  state: ConnectionState;
  onOpenPalette: () => void;
  onOpenHelp: () => void;
  /** 统一搜索入口:携带查询展开完整命令面板(见文件头注释) */
  onOpenSearch: (query: string) => void;
  /** favorites 数据源注入(测试);缺省 GET /api/v1/favorites(§6.19) */
  favoritesProvider?: FavoritesProvider;
  /** 产品按 Spec 使用 palette;inline 保留可复用的紧凑结果形态。 */
  searchMode?: 'palette' | 'inline';
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
  const navigate = useNavigate();
  const {
    state,
    onOpenPalette,
    onOpenHelp,
    onOpenSearch,
    favoritesProvider,
    searchMode = 'inline',
  } = props;
  const [searchValue, setSearchValue] = useState('');
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const context = usePaletteContext();

  const data = usePaletteData({
    workspaceId: context.workspaceId,
    userId: context.userId,
    query: searchValue,
    enabled: popoverOpen,
    favoritesProvider,
  });

  const flat = useMemo(() => flattenSections(data.sections), [data.sections]);
  // 弹层默认无选中:Enter 提交展开完整面板;↑↓ 显式进入弹层选择后方可 Enter 激活(§4.9)。
  const selectedIndex =
    selectedId === null ? -1 : flat.findIndex((option) => option.stableId === selectedId);

  // 弹层外点击关闭(等价鼠标路径的可预期收起)
  useEffect(() => {
    if (!popoverOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent): void => {
      const root = containerRef.current;
      if (root !== null && !root.contains(event.target as Node)) {
        setPopoverOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [popoverOpen]);

  const closePopover = (): void => {
    setPopoverOpen(false);
    setSelectedId(null);
  };

  const handleActivate = (option: PaletteOption, opts: { newTab: boolean }): void => {
    activatePaletteOption(
      option,
      {
        navigate,
        openExternal: (url) => {
          window.open(url, '_blank', 'noopener');
        },
        recordRecent: (target) => {
          if (target.item !== undefined) {
            pushRecent({
              kind: 'object',
              type: target.item.type,
              id: target.item.id,
              title: target.item.title,
              url: target.url,
              at: Date.now(),
            });
          } else if (target.command !== undefined) {
            pushRecent({
              kind: 'command',
              id: target.command.id,
              commandId: target.command.id,
              title: target.command.label,
              at: Date.now(),
            });
          }
        },
        recordCommandUse: trackCommandUse,
        onAfter: () => {
          setSearchValue('');
          closePopover();
        },
      },
      opts,
    );
  };

  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const value = event.target.value;
    if (searchMode === 'palette') {
      if (value === '') {
        setSearchValue('');
        return;
      }
      // §4.9:首字符即把查询与焦点交给完整命令面板;本框不残留第二份状态。
      setSearchValue('');
      closePopover();
      onOpenSearch(value);
      return;
    }
    setSearchValue(value);
    setSelectedId(null);
    setPopoverOpen(value.trim() !== '');
  };

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'ArrowDown' && popoverOpen) {
      event.preventDefault();
      setSelectedId(moveSelection(flat, selectedId, 1));
      return;
    }
    if (event.key === 'ArrowUp' && popoverOpen) {
      event.preventDefault();
      setSelectedId(moveSelection(flat, selectedId, -1));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const target = popoverOpen && selectedIndex >= 0 ? flat[selectedIndex] : undefined;
      if (target !== undefined) {
        handleActivate(target, { newTab: false });
        return;
      }
      // 无选中项:携带查询展开完整命令面板并交接焦点(§4.9 统一入口)
      setPopoverOpen(false);
      setSearchValue('');
      onOpenSearch(searchValue);
      return;
    }
    if (event.key === 'Escape') {
      if (popoverOpen) {
        closePopover();
      } else {
        setSearchValue('');
      }
    }
  };

  return (
    <header className="mesh-topbar" aria-label={t('a11y.topbar')}>
      {/* §4.2:品牌是返回首页的链接 */}
      <NavLink to="/" data-testid="topbar-brand" className="mesh-topbar__brand">
        Mesh
      </NavLink>
      <WorkspaceSwitcher />
      <div className="mesh-topbar__search-wrap" ref={containerRef}>
        <input
          data-testid="topbar-search"
          className="mesh-topbar__search"
          type="search"
          placeholder={t('common.search')}
          aria-label={t('common.search')}
          value={searchValue}
          onChange={handleSearchChange}
          onKeyDown={handleSearchKeyDown}
          aria-expanded={popoverOpen}
          aria-controls={popoverOpen ? listId : undefined}
          aria-activedescendant={
            popoverOpen && selectedId !== null ? `palette-opt-${selectedId}` : undefined
          }
          autoComplete="off"
        />
        {popoverOpen ? (
          <div className="mesh-topbar__search-popover" data-testid="topbar-search-popover">
            {data.flatCount > 0 ? (
              <PaletteResults
                sections={data.sections}
                selectedStableId={selectedId}
                onOptionHover={setSelectedId}
                onOptionActivate={handleActivate}
                isSearching={data.isSearching && searchValue.trim() !== ''}
                skeletonLabel={t('search.loading')}
                listId={listId}
                listLabel={t('common.search')}
              />
            ) : (
              <p className="mesh-palette__empty">{t('shortcuts.paletteEmpty')}</p>
            )}
          </div>
        ) : null}
      </div>
      <span className="mesh-topbar__conn" data-testid="conn-status">
        {TEXT_VISIBLE_STATES.has(state) ? (
          <StatusDot
            tone={TONE_BY_STATE[state]}
            label={t('status.' + state)}
            pulse={PULSING_STATES.has(state)}
          />
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
        <IconButton
          data-testid="open-palette"
          label={t('a11y.openPalette')}
          onClick={onOpenPalette}
        >
          ⌘
        </IconButton>
        <IconButton data-testid="open-help" label={t('a11y.openHelp')} onClick={onOpenHelp}>
          ?
        </IconButton>
      </span>
    </header>
  );
}
