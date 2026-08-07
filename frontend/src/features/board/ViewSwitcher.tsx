/**
 * 视图切换器(kanban.md §4.2 侧栏视图列表):当前视图高亮、默认视图星标、
 * 新建视图、行内操作菜单(重命名 / 复制 / 设默认 / 删除)。
 */
import { useState } from 'react';
import { Button, Dialog, Icon, IconButton, Input, Select } from '../../design';
import type { IconName } from '../../design';
import { useT } from '../../i18n';
import type { View } from './types';

interface ViewSwitcherProps {
  readonly views: readonly View[];
  readonly selectedId: string | null;
  readonly canWrite: (view: View) => boolean;
  readonly onSelect: (viewId: string) => void;
  readonly onCreate: (
    name: string,
    layout: View['layout'],
    visibility: View['visibility'],
  ) => Promise<void>;
  readonly onRename: (view: View, name: string) => Promise<void>;
  readonly onDuplicate: (view: View) => Promise<void>;
  readonly onSetDefault: (view: View) => Promise<void>;
  readonly onDelete: (view: View) => Promise<void>;
  /** L222:已收藏视图 id 集合(⋯ 菜单星标条目);未提供 onToggleFavorite 则不渲染。 */
  readonly favoriteViewIds?: ReadonlySet<string>;
  readonly onToggleFavorite?: (view: View) => void;
}

/* 布局图标一律经设计图标集(§13.2 禁字符图标)。timeline/table 为预留布局,
   取语义最近的图标占位。 */
const LAYOUT_ICONS: Record<View['layout'], IconName> = {
  board: 'board',
  list: 'list',
  timeline: 'calendar',
  table: 'list',
};

export function ViewSwitcher(props: ViewSwitcherProps): React.JSX.Element {
  const {
    views,
    selectedId,
    canWrite,
    onSelect,
    onCreate,
    onRename,
    onDuplicate,
    onSetDefault,
    onDelete,
    favoriteViewIds,
    onToggleFavorite,
  } = props;
  const t = useT();
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createLayout, setCreateLayout] = useState<View['layout']>('board');
  const [createVisibility, setCreateVisibility] = useState<View['visibility']>('private');
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<View | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<View | null>(null);
  const [renameName, setRenameName] = useState('');
  const [busy, setBusy] = useState(false);

  const submitCreate = async (): Promise<void> => {
    const name = createName.trim();
    if (name === '' || busy) return;
    setBusy(true);
    try {
      await onCreate(name, createLayout, createVisibility);
      setCreateOpen(false);
      setCreateName('');
    } finally {
      setBusy(false);
    }
  };

  const submitRename = async (): Promise<void> => {
    const name = renameName.trim();
    if (renameTarget === null || name === '' || busy) return;
    setBusy(true);
    try {
      await onRename(renameTarget, name);
      setRenameTarget(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <nav className="mesh-view-switcher" aria-label={t('board.viewSwitcherLabel')}>
      <ul className="mesh-view-switcher__list" data-testid="view-switcher-list">
        {views.map((view) => {
          const selected = view.id === selectedId;
          return (
            <li key={view.id} className="mesh-view-switcher__item">
              <Button
                variant="ghost"
                className={
                  selected
                    ? 'mesh-view-switcher__entry mesh-view-switcher__entry--active'
                    : 'mesh-view-switcher__entry'
                }
                data-testid={`view-entry-${view.id}`}
                aria-current={selected ? 'true' : undefined}
                onClick={() => onSelect(view.id)}
              >
                <span className="mesh-view-switcher__icon" aria-hidden="true">
                  <Icon name={LAYOUT_ICONS[view.layout]} size={16} />
                </span>
                <span className="mesh-view-switcher__name">{view.name}</span>
                {view.is_default ? (
                  <span className="mesh-view-switcher__default" title={t('board.defaultView')}>
                    <Icon name="star" size={16} filled label={t('board.defaultView')} />
                  </span>
                ) : null}
              </Button>
              {canWrite(view) ? (
                <>
                  <IconButton
                    variant="ghost"
                    size="sm"
                    className="mesh-view-switcher__menu-trigger"
                    label={t('board.viewActions', { name: view.name })}
                    aria-expanded={menuFor === view.id}
                    aria-haspopup="menu"
                    aria-controls={`view-menu-list-${view.id}`}
                    data-testid={`view-menu-${view.id}`}
                    onClick={() => setMenuFor(menuFor === view.id ? null : view.id)}
                  >
                    <Icon name="more-horizontal" size={16} />
                  </IconButton>
                  {menuFor === view.id ? (
                    <ul
                      id={`view-menu-list-${view.id}`}
                      className="mesh-view-switcher__menu"
                      role="menu"
                      data-testid={`view-menu-list-${view.id}`}
                    >
                      <li>
                        <Button
                          variant="ghost"
                          size="sm"
                          role="menuitem"
                          onClick={() => {
                            setRenameTarget(view);
                            setRenameName(view.name);
                            setMenuFor(null);
                          }}
                        >
                          {t('board.renameView')}
                        </Button>
                      </li>
                      <li>
                        <Button
                          variant="ghost"
                          size="sm"
                          role="menuitem"
                          onClick={() => {
                            void onDuplicate(view);
                            setMenuFor(null);
                          }}
                        >
                          {t('board.duplicateView')}
                        </Button>
                      </li>
                      {view.is_default ? null : (
                        <li>
                          <Button
                            variant="ghost"
                            size="sm"
                            role="menuitem"
                            onClick={() => {
                              void onSetDefault(view);
                              setMenuFor(null);
                            }}
                          >
                            {t('board.makeDefault')}
                          </Button>
                        </li>
                      )}
                      {/* L222:收藏视图条目(星标);收藏与删除权限解耦,读权限即可收藏。
                          未提供 onToggleFavorite 的调用方不渲染该条目。 */}
                      {onToggleFavorite === undefined ? null : (
                        <li>
                          <Button
                            variant="ghost"
                            size="sm"
                            role="menuitem"
                            data-testid={`view-favorite-toggle-${view.id}`}
                            onClick={() => {
                              onToggleFavorite(view);
                              setMenuFor(null);
                            }}
                          >
                            {favoriteViewIds?.has(view.id) === true
                              ? t('favorites.remove')
                              : t('favorites.add')}
                          </Button>
                        </li>
                      )}
                      <li>
                        <Button
                          variant="ghost"
                          size="sm"
                          role="menuitem"
                          className="mesh-view-switcher__danger"
                          data-testid={`view-delete-open-${view.id}`}
                          onClick={() => {
                            setDeleteTarget(view);
                            setMenuFor(null);
                          }}
                        >
                          {t('board.deleteView')}
                        </Button>
                      </li>
                    </ul>
                  ) : null}
                </>
              ) : null}
            </li>
          );
        })}
      </ul>
      <Button
        variant="secondary"
        onClick={() => setCreateOpen(true)}
        data-testid="view-create-open"
      >
        <Icon name="plus" size={16} /> {t('board.newView')}
      </Button>

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t('board.newViewTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-view-switcher__dialog">
          <Input
            label={t('board.viewNameLabel')}
            value={createName}
            maxLength={100}
            onChange={(event) => setCreateName(event.target.value)}
            data-testid="view-create-name"
          />
          <Select
            label={t('board.viewLayoutLabel')}
            value={createLayout}
            onChange={(event) => setCreateLayout(event.target.value as View['layout'])}
            data-testid="view-create-layout"
          >
            <option value="board">{t('board.layout.board')}</option>
            <option value="list">{t('board.layout.list')}</option>
          </Select>
          <Select
            label={t('board.viewVisibilityLabel')}
            value={createVisibility}
            onChange={(event) => setCreateVisibility(event.target.value as View['visibility'])}
            data-testid="view-create-visibility"
          >
            <option value="private">{t('board.visibility.private')}</option>
            <option value="shared">{t('board.visibility.shared')}</option>
          </Select>
          <div className="mesh-view-switcher__dialog-actions">
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => void submitCreate()}
              disabled={createName.trim() === '' || busy}
              data-testid="view-create-submit"
            >
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={renameTarget !== null}
        onClose={() => setRenameTarget(null)}
        title={t('board.renameViewTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-view-switcher__dialog">
          <Input
            label={t('board.viewNameLabel')}
            value={renameName}
            maxLength={100}
            onChange={(event) => setRenameName(event.target.value)}
            data-testid="view-rename-name"
          />
          <div className="mesh-view-switcher__dialog-actions">
            <Button variant="secondary" onClick={() => setRenameTarget(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => void submitRename()}
              disabled={renameName.trim() === '' || busy}
              data-testid="view-rename-submit"
            >
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>

      {/* 视图删除确认(§13.3 destructive 明确确认;删除不可撤销) */}
      <Dialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title={t('board.deleteViewConfirmTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-view-switcher__dialog">
          <p data-testid="view-delete-confirm-body">
            {t('board.deleteViewConfirmBody', { name: deleteTarget?.name ?? '' })}
          </p>
          <div className="mesh-view-switcher__dialog-actions">
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              disabled={busy}
              data-testid="view-delete-confirm"
              onClick={() => {
                if (deleteTarget !== null) {
                  void onDelete(deleteTarget);
                }
                setDeleteTarget(null);
              }}
            >
              {t('board.deleteViewConfirm')}
            </Button>
          </div>
        </div>
      </Dialog>
    </nav>
  );
}
