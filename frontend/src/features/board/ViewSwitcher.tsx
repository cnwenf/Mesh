/**
 * 视图切换器(kanban.md §4.2 侧栏视图列表):当前视图高亮、默认视图星标、
 * 新建视图、行内操作菜单(重命名 / 复制 / 设默认 / 删除)。
 */
import { useState } from 'react';
import { Button, Dialog, Input } from '../../design';
import { useT } from '../../i18n';
import type { View } from './types';

interface ViewSwitcherProps {
  readonly views: readonly View[];
  readonly selectedId: string | null;
  readonly canWrite: (view: View) => boolean;
  readonly onSelect: (viewId: string) => void;
  readonly onCreate: (name: string, layout: View['layout'], visibility: View['visibility']) => Promise<void>;
  readonly onRename: (view: View, name: string) => Promise<void>;
  readonly onDuplicate: (view: View) => Promise<void>;
  readonly onSetDefault: (view: View) => Promise<void>;
  readonly onDelete: (view: View) => Promise<void>;
}

const LAYOUT_ICONS: Record<View['layout'], string> = {
  board: '▦',
  list: '☰',
  timeline: '⧗',
  table: '▤',
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
  } = props;
  const t = useT();
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createLayout, setCreateLayout] = useState<View['layout']>('board');
  const [createVisibility, setCreateVisibility] = useState<View['visibility']>('private');
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<View | null>(null);
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
              <button
                type="button"
                className={
                  selected
                    ? 'mesh-view-switcher__entry mesh-view-switcher__entry--active'
                    : 'mesh-view-switcher__entry'
                }
                data-testid={`view-entry-${view.id}`}
                aria-current={selected ? 'true' : undefined}
                onClick={() => onSelect(view.id)}
              >
                <span aria-hidden="true">{LAYOUT_ICONS[view.layout]}</span>
                <span className="mesh-view-switcher__name">{view.name}</span>
                {view.is_default ? (
                  <span className="mesh-view-switcher__default" title={t('board.defaultView')}>
                    ★
                  </span>
                ) : null}
              </button>
              {canWrite(view) ? (
                <>
                  <button
                    type="button"
                    className="mesh-view-switcher__menu-trigger"
                    aria-label={t('board.viewActions', { name: view.name })}
                    aria-expanded={menuFor === view.id}
                    data-testid={`view-menu-${view.id}`}
                    onClick={() => setMenuFor(menuFor === view.id ? null : view.id)}
                  >
                    …
                  </button>
                  {menuFor === view.id ? (
                    <ul className="mesh-view-switcher__menu" data-testid={`view-menu-list-${view.id}`}>
                      <li>
                        <button
                          type="button"
                          onClick={() => {
                            setRenameTarget(view);
                            setRenameName(view.name);
                            setMenuFor(null);
                          }}
                        >
                          {t('board.renameView')}
                        </button>
                      </li>
                      <li>
                        <button
                          type="button"
                          onClick={() => {
                            void onDuplicate(view);
                            setMenuFor(null);
                          }}
                        >
                          {t('board.duplicateView')}
                        </button>
                      </li>
                      {view.is_default ? null : (
                        <li>
                          <button
                            type="button"
                            onClick={() => {
                              void onSetDefault(view);
                              setMenuFor(null);
                            }}
                          >
                            {t('board.makeDefault')}
                          </button>
                        </li>
                      )}
                      <li>
                        <button
                          type="button"
                          className="mesh-view-switcher__danger"
                          onClick={() => {
                            void onDelete(view);
                            setMenuFor(null);
                          }}
                        >
                          {t('board.deleteView')}
                        </button>
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
        + {t('board.newView')}
      </Button>

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} title={t('board.newViewTitle')} closeLabel={t('common.close')}>
        <div className="mesh-view-switcher__dialog">
          <Input
            label={t('board.viewNameLabel')}
            value={createName}
            maxLength={100}
            onChange={(event) => setCreateName(event.target.value)}
            data-testid="view-create-name"
          />
          <label className="mesh-view-switcher__field">
            {t('board.viewLayoutLabel')}
            <select
              value={createLayout}
              onChange={(event) => setCreateLayout(event.target.value as View['layout'])}
              data-testid="view-create-layout"
            >
              <option value="board">{t('board.layout.board')}</option>
              <option value="list">{t('board.layout.list')}</option>
            </select>
          </label>
          <label className="mesh-view-switcher__field">
            {t('board.viewVisibilityLabel')}
            <select
              value={createVisibility}
              onChange={(event) =>
                setCreateVisibility(event.target.value as View['visibility'])
              }
              data-testid="view-create-visibility"
            >
              <option value="private">{t('board.visibility.private')}</option>
              <option value="shared">{t('board.visibility.shared')}</option>
            </select>
          </label>
          <div className="mesh-view-switcher__dialog-actions">
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void submitCreate()} disabled={createName.trim() === '' || busy} data-testid="view-create-submit">
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog open={renameTarget !== null} onClose={() => setRenameTarget(null)} title={t('board.renameViewTitle')} closeLabel={t('common.close')}>
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
            <Button onClick={() => void submitRename()} disabled={renameName.trim() === '' || busy} data-testid="view-rename-submit">
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>
    </nav>
  );
}
