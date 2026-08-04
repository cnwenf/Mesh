/**
 * 命令面板(Ctrl/Cmd+K 打开,README §6.12 / search-command-palette.md §4)。
 *
 * - 本地命令同步过滤零延迟先渲染;实体结果经 usePaletteData(防抖/可取消/旧响应丢弃),
 *   skeleton 不阻塞本地命令(design-quality §11.4 首开交互 ≤100ms);
 * - 空 query 唯一数据流(§4.2.1):favorites → recents → 常用命令;有 query 六类分组 + 命令;
 * - 键盘:↑↓ 循环移动、Enter 激活(keydown 瞬间捕获目标,§4.3.1 竞态安全)、
 *   mod+Enter 新标签打开规范深链、Tab 补全选中标题、Esc 关闭(Dialog);
 * - 异步补入按稳定 id 维持选择(§4.3.1);ARIA combobox/listbox + live region 播报结果数;
 * - 异常态(§4.2):error 行 + 重试;offline 降级本地命令;no-results 语法提示 +
 *   有 issue:write 者可见的「新建 issue」预填入口(不直接提交)。
 *
 * prop 面向后兼容:既有 {open,onClose,closeLabel,searchPlaceholder,emptyText,title,
 * initialQuery} 语义不变;工作区/用户缺省经 usePaletteContext 自解析(App 层可显式覆盖)。
 */
import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { errorToI18nKey } from '../api/errors';
import { Button, Dialog, InputControl, Kbd } from '../design';
import { useT } from '../i18n';
import { PaletteResults } from './PaletteResults';
import {
  activatePaletteOption,
  flattenSections,
  moveSelection,
  reconcileSelection,
} from './paletteModel';
import type { PaletteOption } from './paletteModel';
import { pushRecent, trackCommandUse } from './recents';
import type { RecentEntry } from './recents';
import { detectMac, isComposingEvent } from './ShortcutProvider';
import { usePaletteContext } from './usePaletteContext';
import { isOfflineCondition, usePaletteData } from './usePaletteData';
import type { FavoritesProvider } from './usePaletteData';
import './shortcuts.css';

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  /** 关闭按钮可访问名 */
  closeLabel: string;
  /** 搜索框占位符 */
  searchPlaceholder: string;
  /** 无匹配时的空态文案 */
  emptyText: string;
  /** 面板标题(dialog 可访问名) */
  title: string;
  /**
   * 打开时的初始查询(统一搜索入口:顶栏搜索续输入/回车展开面板时携带已键入文本,
   * search-command-palette.md S1)。仅在 open 由 false→true 时读取;缺省为清空。
   */
  initialQuery?: string;
  /** 工作区 id;缺省经 usePaletteContext(GET /users/me)自解析 */
  workspaceId?: string | null;
  /** 工作区 slug(规范深链组装备用;当前结果 url 由服务端给出) */
  workspaceSlug?: string | null;
  /** 当前用户 id(recents 三元组隔离);缺省自解析 */
  userId?: string | null;
  /** 当前工作区是否有 issue:write(no-results「新建 issue」门控);缺省按角色派生 */
  canCreateIssue?: boolean;
  /** no-results「新建 issue」动作:仅预填创建入口,不直接提交(§4.2) */
  onOpenIssueCreate?: (query: string) => void;
  /** favorites 数据源注入(测试);缺省 GET /api/v1/favorites(§6.19) */
  favoritesProvider?: FavoritesProvider;
}

const SEARCH_ITEM_TYPES: ReadonlySet<string> = new Set([
  'issue',
  'member',
  'agent',
  'project',
  'view',
  'chat_session',
]);

function subscribeOnline(callback: () => void): () => void {
  window.addEventListener('online', callback);
  window.addEventListener('offline', callback);
  return () => {
    window.removeEventListener('online', callback);
    window.removeEventListener('offline', callback);
  };
}

function getIsOnline(): boolean {
  return typeof navigator === 'undefined' ? true : navigator.onLine;
}

/** 选项 → recent 条目(命令/实体/收藏与无 item 的对象选项) */
function recentEntryForOption(option: PaletteOption, at: number): RecentEntry | null {
  if (option.command !== undefined) {
    return {
      kind: 'command',
      id: option.command.id,
      commandId: option.command.id,
      title: option.command.label,
      at,
    };
  }
  if (option.item !== undefined) {
    return {
      kind: 'object',
      type: option.item.type,
      id: option.item.id,
      title: option.item.title,
      url: option.url,
      at,
    };
  }
  if (option.url === undefined) {
    return null;
  }
  // favorites / recents 组选项:stableId 形如 `fav:{type}:{id}` 或 `{type}:{id}`
  const parts = option.stableId.split(':');
  const rawType = parts[0] === 'fav' ? (parts[1] ?? '') : parts[0];
  const type = SEARCH_ITEM_TYPES.has(rawType) ? (rawType as RecentEntry['type']) : undefined;
  const id = parts[0] === 'fav' ? (parts[2] ?? '') : parts.slice(1).join(':');
  if (id === '') {
    return null;
  }
  return { kind: 'object', type, id, title: option.title, url: option.url, at };
}

export function CommandPalette(props: CommandPaletteProps): React.JSX.Element | null {
  const {
    open,
    onClose,
    closeLabel,
    searchPlaceholder,
    emptyText,
    title,
    initialQuery,
    favoritesProvider,
  } = props;
  // 注:workspaceSlug 为附加公开 prop(规范深链组装备用);当前结果 url 由服务端
  // 规范深链给出,渲染不消费它(保留 prop 面以便 App 层显式传入,见接线说明)。
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const context = usePaletteContext(location.pathname);
  const workspaceId = props.workspaceId !== undefined ? props.workspaceId : context.workspaceId;
  const workspaceSlug =
    props.workspaceSlug !== undefined ? props.workspaceSlug : context.workspaceSlug;
  const userId = props.userId !== undefined ? props.userId : context.userId;
  const canCreateIssue =
    props.canCreateIssue ?? (context.role !== null && context.role !== 'guest');

  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const lastIndexRef = useRef(0);
  // 派生选中项的稳定 id(用户未显式移动时的当前选中);异步补入按稳定 id 收敛,
  // 使实体插入不移动用户即将 Enter 的条目(§4.3.1.4 选中不移位)
  const lastStableIdRef = useRef<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const isOnline = useSyncExternalStore(subscribeOnline, getIsOnline, () => true);

  const data = usePaletteData({
    workspaceId,
    workspaceSlug,
    userId,
    query,
    enabled: open && isOnline,
    favoritesProvider,
  });

  const flat = useMemo(() => flattenSections(data.sections), [data.sections]);
  const selection = useMemo(
    () => reconcileSelection(flat, selectedId ?? lastStableIdRef.current, lastIndexRef.current),
    [flat, selectedId],
  );

  const trimmed = query.trim();

  useEffect(() => {
    if (open) {
      setQuery(initialQuery ?? '');
      setSelectedId(null);
      lastIndexRef.current = 0;
      lastStableIdRef.current = null;
    }
  }, [open, initialQuery]);

  useEffect(() => {
    setSelectedId(null);
    lastStableIdRef.current = null;
  }, [query]);

  // 选中追踪须在上方两个重置 effect 之后声明(React 按声明序执行 effect):
  // 重置后的首帧即把派生选中记入 ref,异步补入方能按稳定 id 收敛(§4.3.1.4)
  useEffect(() => {
    lastIndexRef.current = selection.index < 0 ? 0 : selection.index;
    lastStableIdRef.current = selection.stableId;
  }, [selection.index, selection.stableId]);

  // live region:每次检索落地播报结果数(§9.6 第 7 点)
  useEffect(() => {
    if (!open) {
      setAnnouncement('');
      return;
    }
    if (data.isSearching) {
      return;
    }
    setAnnouncement(t('search.resultsCount', { count: data.flatCount }));
  }, [open, data.isSearching, data.settledToken, data.flatCount, t]);

  if (!open) {
    return null;
  }

  const offline = isOfflineCondition(isOnline, data.error);
  const showNoResults =
    trimmed !== '' && data.flatCount === 0 && !data.isSearching && data.error === null;
  const activeDescendant =
    selection.stableId !== null ? `palette-opt-${selection.stableId}` : undefined;

  const handleActivate = (option: PaletteOption, opts: { newTab: boolean }): void => {
    activatePaletteOption(
      option,
      {
        navigate,
        openExternal: (url) => {
          window.open(url, '_blank', 'noopener');
        },
        recordRecent: (target) => {
          const entry = recentEntryForOption(target, Date.now());
          if (entry !== null) {
            pushRecent(entry);
          }
        },
        recordCommandUse: trackCommandUse,
        onAfter: () => {
          data.noteLocalChange();
          onClose();
        },
      },
      opts,
    );
  };

  const handleCreateIssue = (): void => {
    if (props.onOpenIssueCreate !== undefined) {
      props.onOpenIssueCreate(trimmed);
      return;
    }
    navigate(`/issues?create=1&title=${encodeURIComponent(trimmed)}`);
    onClose();
  };

  const handleInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>): void => {
    if (isComposingEvent(event.nativeEvent)) {
      // Escape 在输入法候选期只交给 IME；不冒泡到 Dialog 的失焦/关闭栈。
      // Tab 仍交给 Dialog 做焦点圈养，避免组合输入时焦点逃出 modal。
      if (event.key === 'Escape') {
        event.stopPropagation();
      }
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setSelectedId(moveSelection(flat, selection.stableId, 1));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setSelectedId(moveSelection(flat, selection.stableId, -1));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const target = selection.index >= 0 ? flat[selection.index] : undefined;
      if (target === undefined) {
        return; // 无选项:不关闭不执行(既有语义)
      }
      const modPressed = detectMac() ? event.metaKey : event.ctrlKey;
      handleActivate(target, { newTab: modPressed && target.url !== undefined });
      return;
    }
    if (event.key === 'Tab') {
      const target = selection.index >= 0 ? flat[selection.index] : undefined;
      if (target !== undefined) {
        event.preventDefault();
        event.stopPropagation(); // 先于 Dialog 焦点圈养消费 Tab
        setQuery(target.title);
      }
      return;
    }
    if (event.key === 'Escape') {
      // §4.5 分层关闭:输入框获焦时首个 Esc 只把焦点交回对话框,
      // 不让事件冒泡到 Dialog 的关闭处理;第二个 Esc 再关闭面板。
      event.preventDefault();
      event.stopPropagation();
      const dialog = inputRef.current?.closest<HTMLElement>('.mesh-dialog');
      if (dialog !== null && dialog !== undefined) {
        dialog.focus();
      } else {
        inputRef.current?.blur();
      }
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      closeLabel={closeLabel}
      initialFocusRef={inputRef}
    >
      <div className="mesh-palette">
        {data.isSearching && trimmed !== '' ? (
          <div className="mesh-palette__progress" aria-hidden="true" />
        ) : null}
        <InputControl
          ref={inputRef}
          type="text"
          size="lg"
          role="combobox"
          className="mesh-palette__input"
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleInputKeyDown}
          aria-expanded={data.flatCount > 0}
          aria-controls={listId}
          aria-activedescendant={activeDescendant}
          autoComplete="off"
        />
        {trimmed === '' && data.flatCount === 0 ? (
          <p className="mesh-palette__empty">{emptyText}</p>
        ) : null}
        {trimmed !== '' && offline ? (
          <p className="mesh-palette__offline" data-testid="palette-offline">
            {t('search.offlineNote')}
          </p>
        ) : null}
        {trimmed !== '' && !offline && data.error !== null ? (
          <div className="mesh-palette__error" role="alert" data-testid="palette-error">
            <span>{t(errorToI18nKey(data.error))}</span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mesh-palette__retry"
              onClick={data.retry}
            >
              {t('search.retry')}
            </Button>
          </div>
        ) : null}
        {data.flatCount > 0 ? (
          <PaletteResults
            sections={data.sections}
            selectedStableId={selection.stableId}
            onOptionHover={setSelectedId}
            onOptionActivate={handleActivate}
            isSearching={data.isSearching && trimmed !== ''}
            skeletonLabel={t('search.loading')}
            listId={listId}
            listLabel={title}
          />
        ) : null}
        {showNoResults ? (
          <div className="mesh-palette__no-results" data-testid="palette-no-results">
            <p className="mesh-palette__empty">{emptyText}</p>
            <p className="mesh-palette__no-results-title">
              {t('search.noResults', { q: trimmed })}
            </p>
            <p className="mesh-palette__no-results-hints">{t('search.noResultsHints')}</p>
            {canCreateIssue ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="mesh-palette__create"
                data-testid="palette-create-issue"
                onClick={handleCreateIssue}
              >
                {t('search.createIssue', { q: trimmed })}
              </Button>
            ) : null}
          </div>
        ) : null}
        <div aria-live="polite" className="sr-only" data-testid="palette-live">
          {announcement}
        </div>
        <div className="mesh-palette__footer">
          <span className="mesh-palette__hint">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            {t('search.hintNav')}
          </span>
          <span className="mesh-palette__hint">
            <Kbd>Enter</Kbd>
            {t('search.hintEnter')}
          </span>
          <span className="mesh-palette__hint">
            <Kbd>Tab</Kbd>
            {t('search.hintTab')}
          </span>
          <span className="mesh-palette__hint">
            <Kbd>?</Kbd>
            {t('search.hintHelp')}
          </span>
        </div>
      </div>
    </Dialog>
  );
}

export type { FavoritesProvider };
