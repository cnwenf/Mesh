/**
 * 统一命令面板(search-command-palette.md §4.1 / §4.2 / §4.2.1 / §4.7):
 * Ctrl/Cmd+K 打开的跨模块搜索 + 命令执行单一入口。
 *
 * 数据流:
 * - 空 query → 唯一组装流(§4.2.1):favorites(§6.19 端点,唯一服务端来源,时间倒序)
 *   → recents(本地三元组隔离,与 favorites 同 target 去重)→ 常用命令(本地计数频次倒序);
 * - 非空 query → 本地命令同步过滤(零延迟先渲染,§4.7)+ 实体结果经 useEntitySearch
 *   (防抖 150ms + 过期取消)异步补入,按类型分组(组头 search.group.*)。
 *
 * 交互与无障碍:
 * - ARIA combobox + listbox:aria-expanded/aria-controls/aria-activedescendant;
 *   选项 role=option + aria-selected;结果变化经 aria-live=polite 播报条数;
 * - ArrowUp/Down 循环移动;Enter 执行(keydown 瞬间捕获选中行,补入竞态不移位,§4.3.1);
 *   Tab 补全选中标题到输入框;Esc 分层关闭(输入框获焦时先失焦,再关面板,§4.5);
 * - mod+Enter / mod+click 新标签打开规范深链(window.open noopener);普通 Enter/click
 *   当前页直达(react-router navigate)并记录 recent(§1.3 修饰键新标签);
 * - 命中标题以字重 + 下划线叠加高亮(颜色不作唯一信号,§6.12);徽章/副标题经消息目录
 *   本地化组装(§6.18);颜色一律语义 token,无硬编码。
 *
 * 状态:loading 顶部细进度条;no-results(**检索已完成且结果空**方呈现——在途/防抖
 * 窗口不渲染,+「新建 issue」预填动作仅 canCreateIssue 者可见,§4.2);error + 重试;
 * offline 提示(命令仍可用)。prefers-reduced-motion 降级见 shortcuts.css。
 */
import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
} from 'react';
import { useNavigate } from 'react-router';
import type { MeshApiClient } from '../api/client';
import { getApiClient } from '../api/instance';
import { Dialog } from '../design/components/Dialog';
import { listAllFavorites } from '../features/search/api';
import { incrementCommandCount, readCommandCounts } from '../features/search/commandCounts';
import { collectValidRecentKeys, resolveFavoriteTargets } from '../features/search/favoritesResolve';
import type { ResolvedTarget } from '../features/search/favoritesResolve';
import {
  getPaletteQuery,
  subscribePaletteQuery,
  takePaletteQuery,
} from '../features/search/paletteBridge';
import {
  buildEmptyQueryRows,
  buildQueryRows,
  entityRowKey,
  filterCommands,
  groupEntityResults,
} from '../features/search/paletteSections';
import type { PaletteRow } from '../features/search/paletteSections';
import { agentCapacityText, badgeText, entitySubtitle, glyphFor, splitHighlight } from '../features/search/paletteText';
import { pruneRecents, readRecents, recentTargetKey, recordRecent } from '../features/search/recents';
import type { RecentEntry } from '../features/search/recents';
import type { FavoriteEntry } from '../features/search/types';
import { IDENTIFIER_QUERY_PATTERN, useEntitySearch } from '../features/search/useEntitySearch';
import { usePaletteIdentity } from '../features/search/usePaletteIdentity';
import { useT } from '../i18n';
import { useShortcutRegistry } from './registry';
import { detectMac, formatCombo } from './ShortcutProvider';
import './shortcuts.css';

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  /** 关闭按钮可访问名 */
  closeLabel: string;
  /** 搜索框占位符 */
  searchPlaceholder: string;
  /** 无任何可展示行时的空态文案 */
  emptyText: string;
  /** 面板标题(dialog 可访问名) */
  title: string;
  /** 当前工作区是否有 issue:write(门控 no-results 的「新建 issue」动作,§4.2) */
  canCreateIssue?: boolean;
  /** 测试可注入客户端;缺省全局单例 */
  client?: MeshApiClient;
  /**
   * 打开时的初始查询(统一搜索入口:顶栏搜索续输入/回车展开面板时携带已键入文本,
   * search-command-palette.md S1)。非空时优先于顶栏桥接查询;缺省回落桥接/清空。
   */
  initialQuery?: string;
}

/** 行分区呈现计划:组头(消息目录键)+ 区内行 */
interface SectionPlan {
  readonly labelKey: string;
  readonly rows: readonly PaletteRow[];
}

function optionId(rowKey: string): string {
  return `mesh-palette-option-${rowKey}`;
}

/** 行可展示标题(命令取 label;实体/最近/收藏取各自标题) */
function rowTitle(row: PaletteRow): string {
  switch (row.kind) {
    case 'command':
      return row.command.label;
    case 'favorite':
      return row.title;
    case 'recent':
      return row.recent.title;
    case 'entity':
      return row.item.title;
  }
}

/** 行规范深链(命令无深链 → null) */
function rowUrl(row: PaletteRow): string | null {
  switch (row.kind) {
    case 'command':
      return null;
    case 'favorite':
      return row.url;
    case 'recent':
      return row.recent.url;
    case 'entity':
      return row.item.url;
  }
}

function subscribeOnline(callback: () => void): () => void {
  window.addEventListener('online', callback);
  window.addEventListener('offline', callback);
  return () => {
    window.removeEventListener('online', callback);
    window.removeEventListener('offline', callback);
  };
}

/** 网络在线态(§4.2 offline 状态:navigator.onLine + 事件订阅,并发渲染安全) */
function useOnlineStatus(): boolean {
  return useSyncExternalStore(
    subscribeOnline,
    () => navigator.onLine,
    () => true,
  );
}

export function CommandPalette(props: CommandPaletteProps): React.JSX.Element | null {
  const { open, onClose, closeLabel, searchPlaceholder, emptyText, title, canCreateIssue = false, initialQuery } = props;
  const t = useT();
  const navigate = useNavigate();
  const client = props.client ?? getApiClient();
  const identity = usePaletteIdentity({ client });
  const isMac = useMemo(() => detectMac(), []);
  const online = useOnlineStatus();

  const commands = useShortcutRegistry((state) => state.commands);
  const [query, setQuery] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [counts, setCounts] = useState<Readonly<Record<string, number>>>({});
  const [favorites, setFavorites] = useState<readonly FavoriteEntry[]>([]);
  const [recents, setRecents] = useState<readonly RecentEntry[]>([]);
  // 收藏目标批量解析结果(§4.2.1 步骤 3);null = 尚未完成(打开瞬间),map = 已核验。
  const [resolvedFavorites, setResolvedFavorites] = useState<ReadonlyMap<
    string,
    ResolvedTarget
  > | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  const trimmed = query.trim();
  const search = useEntitySearch({
    client,
    workspaceId: identity.workspaceId,
    query: trimmed,
    enabled: open,
  });

  // 打开:消费顶栏桥接查询(§4.9 输入即展开同一视图)、复位选择、聚焦输入框、
  // 加载本地 recents 与服务端 favorites(§4.2.1 唯一数据流)。统一搜索入口
  // (MES-111 S1)经 initialQuery 携带顶栏已键入文本,非空时优先于桥接查询。
  //
  // 依赖只取 open/initialQuery:**绝不**把 identity.userId 列入——users/me 慢解析
  // 晚于用户键入抵达时,重跑会消费已空的顶栏桥并 setQuery('') 抹掉用户已键入的
  // 查询(§4.9 键入即面板状态唯一真源;慢网实测竞态)。命令次数按 userId 的刷新
  // 由下方独立 effect 承担(身份抵达即更新,不触查询/选择/焦点)。
  useEffect(() => {
    if (!open) return;
    const bridged = takePaletteQuery();
    setQuery(initialQuery !== undefined && initialQuery !== '' ? initialQuery : bridged);
    setSelectedKey(null);
    // 聚焦延后一拍:Dialog 自身的焦点移入效果(及 StrictMode 双调用下的焦点
    // 归还/重设)在同步 effect 阶段与输入框聚焦竞争,macrotask 延迟确保输入框最终获焦。
    const timer = setTimeout(() => {
      inputRef.current?.focus();
    }, 0);
    return () => clearTimeout(timer);
  }, [open, initialQuery]);

  // 命令运行次数为三元组用户维的本地增强(§4.2):身份解析抵达即按其 userId 刷新,
  // 与打开 effect 解耦(打开 effect 重跑不得抹除已键入查询,见上注)。
  useEffect(() => {
    setCounts(readCommandCounts(identity.userId));
  }, [identity.userId]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const scope = {
      workspaceId: identity.workspaceId,
      workspaceSlug: identity.workspaceSlug,
    };
    const initialRecents = readRecents(identity.userId, identity.workspaceId);
    setRecents(initialRecents);
    setResolvedFavorites(null);
    // §5.1 打开即清理:批量存在性核验 recents,missing(已删/失权)立即剪枝;
    // 瞬态错误(error)保留,避免网络抖动误删本地数据。
    void collectValidRecentKeys(client, initialRecents, scope).then((validKeys) => {
      if (!cancelled) setRecents(pruneRecents(identity.userId, identity.workspaceId, validKeys));
    });
    // §4.2.1 步骤 3:收藏仅返回 target id,空态批量解析标题/规范深链并剔除失效目标。
    void listAllFavorites(client, identity.workspaceId)
      .then(async (list) => {
        if (cancelled) return;
        setFavorites(list);
        const resolved = await resolveFavoriteTargets(client, list, scope);
        if (!cancelled) setResolvedFavorites(resolved);
      })
      .catch(() => {
        if (!cancelled) {
          setFavorites([]);
          setResolvedFavorites(new Map());
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, client, identity.userId, identity.workspaceId, identity.workspaceSlug]);

  // 顶栏桥接查询在面板已打开时变化(程序化 setPaletteQuery)→ 同步到输入框。
  useEffect(() => {
    if (!open) return;
    return subscribePaletteQuery(() => {
      const bridged = getPaletteQuery();
      if (bridged !== '') {
        setQuery(takePaletteQuery());
      }
    });
  }, [open]);

  // 惰性失效清理(§4.2.1):完整 identifier 检索无命中时,本地指向该 identifier 的
  // recent 已被删/失权 → 经 pruneRecents 剔除(被删对象不残留)。
  useEffect(() => {
    if (trimmed === '' || !IDENTIFIER_QUERY_PATTERN.test(trimmed)) return;
    if (search.loading || search.error !== null) return;
    const upper = trimmed.toUpperCase();
    const checkable = recents.filter((entry) => entry.url.includes(`/by-identifier/${upper}`));
    if (checkable.length === 0) return;
    const foundKeys = new Set(search.entityResults.map((item) => recentTargetKey(item.type, item.id)));
    const invalidKeys = checkable
      .map((entry) => recentTargetKey(entry.type, entry.id))
      .filter((key) => !foundKeys.has(key));
    if (invalidKeys.length === 0) return;
    const invalid = new Set(invalidKeys);
    const validIds = new Set(
      recents.map((entry) => recentTargetKey(entry.type, entry.id)).filter((key) => !invalid.has(key)),
    );
    setRecents(pruneRecents(identity.userId, identity.workspaceId, validIds));
  }, [trimmed, search.loading, search.error, search.entityResults, recents, identity.userId, identity.workspaceId]);

  // 行组装:空 query 走唯一组装流;非空 query 命令同步先行 + 实体分组随后。
  const rows = useMemo<readonly PaletteRow[]>(() => {
    if (trimmed === '') {
      return buildEmptyQueryRows({
        favorites,
        recents,
        commands,
        counts,
        resolved: resolvedFavorites ?? undefined,
      });
    }
    return buildQueryRows(filterCommands(commands, trimmed), search.entityResults);
  }, [trimmed, favorites, recents, commands, counts, resolvedFavorites, search.entityResults]);

  const sections = useMemo<readonly SectionPlan[]>(() => {
    if (trimmed === '') {
      const byKind = {
        favorite: rows.filter((row) => row.kind === 'favorite'),
        recent: rows.filter((row) => row.kind === 'recent'),
        command: rows.filter((row) => row.kind === 'command'),
      };
      return [
        { labelKey: 'search.group.favorite', rows: byKind.favorite },
        { labelKey: 'search.group.recent', rows: byKind.recent },
        { labelKey: 'search.group.command', rows: byKind.command },
      ].filter((section) => section.rows.length > 0);
    }
    const commandRows = rows.filter((row) => row.kind === 'command');
    const entitySections = groupEntityResults(search.entityResults).map((group) => ({
      labelKey: `search.group.${group.type}`,
      rows: group.items.map(
        (item): PaletteRow => ({ kind: 'entity', key: entityRowKey(item), item }),
      ),
    }));
    return [
      ...(commandRows.length > 0 ? [{ labelKey: 'search.group.command', rows: commandRows }] : []),
      ...entitySections,
    ];
  }, [trimmed, rows, search.entityResults]);

  // 选择稳定性(§4.3.1):按稳定 key 维持选中 —— 异步补入不移位当前选中项;
  // 选中行消失(过滤变化/结果集更替)回落首行。注意:selectedKey 可能暂指当前 rows
  // 之外的 key(例如跨次打开面板残留的悬停选中),此时不得以其钉住有效下标,否则
  // Enter 会命中错误行;视为未选中,回落首行(仍存在的 key 经 indexByKey 命中,稳定性不破)。
  const indexByKey = useMemo(
    () => new Map<string, number>(rows.map((row, index) => [row.key, index])),
    [rows],
  );
  const selectedIndex =
    selectedKey === null ? -1 : (indexByKey.has(selectedKey) ? indexByKey.get(selectedKey)! : -1);
  const effectiveIndex = selectedIndex >= 0 ? selectedIndex : rows.length > 0 ? 0 : -1;
  const selectedRow = effectiveIndex >= 0 ? (rows[effectiveIndex] ?? null) : null;
  const selectedRowRef = useRef<PaletteRow | null>(null);
  selectedRowRef.current = selectedRow;

  useEffect(() => {
    setSelectedKey(null);
  }, [query]);

  useEffect(() => {
    if (selectedKey === null) return;
    const element = document.getElementById(optionId(selectedKey));
    // jsdom 无 scrollIntoView;真实浏览器恒有(可选调用仅为环境兼容)
    element?.scrollIntoView?.({ block: 'nearest' });
  }, [selectedKey, effectiveIndex]);

  if (!open) {
    return null;
  }

  const moveSelection = (delta: number): void => {
    if (rows.length === 0) return;
    const next = (effectiveIndex + delta + rows.length) % rows.length;
    const target = rows[next];
    if (target !== undefined) setSelectedKey(target.key);
  };

  const activateRow = (row: PaletteRow): void => {
    switch (row.kind) {
      case 'command': {
        const nextCounts = incrementCommandCount(identity.userId, row.command.id);
        setCounts(nextCounts);
        row.command.run();
        onClose();
        return;
      }
      case 'entity': {
        setRecents(recordRecent(identity.userId, identity.workspaceId, row.item));
        navigate(row.item.url);
        onClose();
        return;
      }
      case 'recent': {
        setRecents(recordRecent(identity.userId, identity.workspaceId, row.recent));
        navigate(row.recent.url);
        onClose();
        return;
      }
      case 'favorite': {
        if (row.url === null) return; // 无可解析深链:不假装导航
        setRecents(
          recordRecent(identity.userId, identity.workspaceId, {
            type: row.targetType,
            id: row.favorite.target_id,
            title: row.title,
            url: row.url,
          }),
        );
        navigate(row.url);
        onClose();
        return;
      }
    }
  };

  const openRowInNewTab = (row: PaletteRow): void => {
    const url = rowUrl(row);
    if (url === null) {
      activateRow(row); // 命令无深链:修饰键等价于直接执行
      return;
    }
    window.open(url, '_blank', 'noopener');
  };

  const handleCreateIssue = (): void => {
    navigate(`/issues?create=1&title=${encodeURIComponent(trimmed)}`);
    onClose();
  };

  const handleInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveSelection(1);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveSelection(-1);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      // §4.3.1:以 keydown 瞬间的选中行为目标,异步补入竞态不得替换之
      const row = selectedRowRef.current;
      if (row === null) return;
      if (event.metaKey || event.ctrlKey) {
        openRowInNewTab(row);
      } else {
        activateRow(row);
      }
      return;
    }
    if (event.key === 'Tab') {
      const row = selectedRowRef.current;
      if (row !== null) {
        event.preventDefault();
        setQuery(rowTitle(row));
      }
      return;
    }
    if (event.key === 'Escape') {
      // Esc 分层关闭栈(§4.5):输入框获焦时首个 Esc 仅失焦(焦点落对话框),不关面板
      event.stopPropagation();
      const dialog = inputRef.current?.closest<HTMLElement>('.mesh-dialog');
      if (dialog !== null && dialog !== undefined) {
        dialog.focus();
      } else {
        inputRef.current?.blur();
      }
    }
  };

  const handleRowClick = (event: ReactMouseEvent<HTMLLIElement>, row: PaletteRow): void => {
    if (event.metaKey || event.ctrlKey) {
      openRowInNewTab(row);
    } else {
      activateRow(row);
    }
  };

  // no-results 仅在「检索已完成且结果空」时呈现(§4.2):防抖窗口与在途请求期间
  // (settled=false / loading)一律不渲染,杜绝 no-results 文案与「新建 issue」按钮
  // 同真实结果行短暂并存(含查询文本的元素瞬态重复,strict mode 歧义)。
  const showNoResults =
    trimmed !== '' &&
    search.settled &&
    !search.loading &&
    search.error === null &&
    rows.length === 0;

  return (
    <Dialog open={open} onClose={onClose} title={title} closeLabel={closeLabel}>
      <div className="mesh-palette">
        {search.loading ? (
          <div className="mesh-palette__progress" role="progressbar" aria-label={t('search.loading')} />
        ) : null}
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          className="mesh-palette__input"
          placeholder={searchPlaceholder}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleInputKeyDown}
          aria-expanded={rows.length > 0}
          aria-controls={listId}
          aria-activedescendant={selectedRow !== null ? optionId(selectedRow.key) : undefined}
          autoComplete="off"
        />
        <p className="mesh-palette__live" aria-live="polite">
          {trimmed !== '' ? t('search.resultCount', { count: rows.length }) : ''}
        </p>
        {!online ? <p className="mesh-palette__notice">{t('search.offline')}</p> : null}
        {trimmed !== '' && search.error !== null ? (
          <div className="mesh-palette__error" role="alert">
            <span>{t('search.error')}</span>
            <button type="button" className="mesh-palette__retry" onClick={search.retry}>
              {t('search.retry')}
            </button>
          </div>
        ) : null}
        {showNoResults ? (
          <div className="mesh-palette__noresults">
            <p>{t('search.noResults', { q: trimmed })}</p>
            <p className="mesh-palette__hint">{t('search.noResultsHint')}</p>
            {canCreateIssue ? (
              <button type="button" className="mesh-palette__create" onClick={handleCreateIssue}>
                {t('search.createIssue', { q: trimmed })}
              </button>
            ) : null}
          </div>
        ) : null}
        {/* 空态文案仅空 query(§4.2 empty 态);非空 query 的在途/防抖窗口由 loading 态
            覆盖(不渲染空态),完成后由 no-results / error 接管。 */}
        {trimmed === '' && rows.length === 0 ? (
          <p className="mesh-palette__empty">{emptyText}</p>
        ) : null}
        {rows.length > 0 ? (
          <ul id={listId} role="listbox" className="mesh-palette__list" aria-label={title}>
            {sections.map((section) => (
              <li key={`section-${section.labelKey}`} role="presentation" className="mesh-palette__section">
                <span className="mesh-palette__section-title">{t(section.labelKey)}</span>
                <ul role="presentation" className="mesh-palette__section-list">
                  {section.rows.map((row) => {
                    const isActive = (indexByKey.get(row.key) ?? -1) === effectiveIndex;
                    return (
                      <li
                        key={row.key}
                        id={optionId(row.key)}
                        role="option"
                        aria-selected={isActive}
                        data-row-key={row.key}
                        className={
                          isActive
                            ? 'mesh-palette__option mesh-palette__option--active'
                            : 'mesh-palette__option'
                        }
                        onMouseEnter={() => setSelectedKey(row.key)}
                        onClick={(event) => handleRowClick(event, row)}
                      >
                        <PaletteRowContent row={row} isMac={isMac} />
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Dialog>
  );
}

/** 行内容:图标字形 + 标题(命中高亮)+ 副标题/徽章/快捷键提示(右对齐) */
function PaletteRowContent(props: { row: PaletteRow; isMac: boolean }): React.JSX.Element {
  const { row, isMac } = props;
  const t = useT();

  if (row.kind === 'command') {
    return (
      <>
        <span className="mesh-palette__icon" aria-hidden="true">
          {glyphFor('command')}
        </span>
        <span className="mesh-palette__label">{row.command.label}</span>
        {row.command.combo !== undefined ? (
          <span className="mesh-palette__combo">{formatCombo(row.command.combo, isMac)}</span>
        ) : null}
      </>
    );
  }

  if (row.kind === 'recent' || row.kind === 'favorite') {
    const glyphKind = row.kind === 'recent' ? row.recent.type : row.targetType;
    return (
      <>
        <span className="mesh-palette__icon" aria-hidden="true">
          {glyphFor(glyphKind)}
        </span>
        <span className="mesh-palette__label">{rowTitle(row)}</span>
      </>
    );
  }

  const { item } = row;
  const spans = splitHighlight(item.title, item.highlight?.title?.ranges);
  const subtitle = entitySubtitle(t, item);
  const capacity = agentCapacityText(t, item);
  return (
    <>
      <span className="mesh-palette__icon" aria-hidden="true">
        {glyphFor(item.type)}
      </span>
      <span className="mesh-palette__main">
        <span className="mesh-palette__title">
          {spans.map((span, index) =>
            span.hit ? (
              <span key={`span-${String(index)}`} className="mesh-palette__hit">
                {span.text}
              </span>
            ) : (
              <span key={`span-${String(index)}`}>{span.text}</span>
            ),
          )}
        </span>
        <span className="mesh-palette__subtitle">
          {subtitle}
          {capacity !== null ? <span className="mesh-palette__capacity"> {capacity}</span> : null}
        </span>
      </span>
      {item.badge !== undefined ? (
        <span className={`mesh-palette__badge mesh-palette__badge--${item.badge.color}`}>
          {badgeText(t, item.badge)}
        </span>
      ) : null}
    </>
  );
}
