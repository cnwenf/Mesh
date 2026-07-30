/**
 * Issue 列表「保存视图」控件(design-quality.md §3.2:保存视图)。
 * 经 Menu 应用/删除命名预设,「保存当前视图」弹 Dialog 录入名称(边界校验:
 * 名称非空)。预设持久化由父组件经 issuesSavedViews 助手负责(本组件纯交互)。
 */
import { useState } from 'react';
import { Button, Dialog, Icon, Menu } from '../../design';
import type { MenuEntry } from '../../design';
import { useT } from '../../i18n';
import type { SavedView } from './issuesSavedViews';
import './issues.css';

interface IssuesSavedViewsControlProps {
  readonly views: readonly SavedView[];
  readonly onApply: (view: SavedView) => void;
  readonly onSave: (name: string) => void;
  readonly onDelete: (name: string) => void;
}

export function IssuesSavedViewsControl(props: IssuesSavedViewsControlProps): React.JSX.Element {
  const t = useT();
  const [saveOpen, setSaveOpen] = useState(false);
  const [name, setName] = useState('');

  const entries: readonly MenuEntry[] = [
    {
      key: '__save_current__',
      label: t('patterns.saveView'),
      icon: 'plus',
      onSelect: () => {
        setName('');
        setSaveOpen(true);
      },
    },
    { separator: true, key: '__sep__' },
    ...props.views.map((view) => ({
      key: `apply:${view.name}`,
      label: view.name,
      onSelect: () => props.onApply(view),
    })),
  ];

  // 每个预设的删除项:与应用项并列(菜单承载低频管理操作,§7.5)。
  const deleteEntries: readonly MenuEntry[] = props.views.map((view) => ({
    key: `delete:${view.name}`,
    label: t('patterns.deleteView', { name: view.name }),
    danger: true,
    onSelect: () => props.onDelete(view.name),
  }));

  const submit = (): void => {
    const trimmed = name.trim();
    if (trimmed === '') return;
    props.onSave(trimmed);
    setSaveOpen(false);
  };

  return (
    <div className="mesh-issues__saved-views">
      <Menu
        triggerLabel={t('patterns.savedViews')}
        trigger={
          <>
            <Icon name="filter" size={16} />
            {t('patterns.savedViews')}
          </>
        }
        entries={entries}
      />
      {props.views.length > 0 ? (
        <Menu
          triggerLabel={t('patterns.deleteView', { name: '' })}
          trigger={<Icon name="trash" size={16} />}
          entries={deleteEntries}
          align="end"
        />
      ) : null}
      <Dialog
        open={saveOpen}
        onClose={() => setSaveOpen(false)}
        title={t('patterns.saveView')}
        closeLabel={t('common.close')}
      >
        <form
          className="mesh-issues__saved-view-form"
          data-testid="saved-view-form"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label className="mesh-issues__field">
            <span>{t('patterns.viewName')}</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t('patterns.viewName')}
              data-testid="saved-view-name"
              autoFocus
            />
          </label>
          <div className="mesh-issues__confirm-actions">
            <Button type="submit" disabled={name.trim() === ''} data-testid="saved-view-save">
              {t('common.save')}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setSaveOpen(false)}>
              {t('common.cancel')}
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
