/**
 * 视图保存条(kanban.md §4.2):配置改动未保存时呈现「保存 / 另存为 / 丢弃」。
 */
import { Button } from '../../design';
import { useT } from '../../i18n';

interface ViewSaveBarProps {
  readonly dirty: boolean;
  readonly busy: boolean;
  readonly canWrite: boolean;
  readonly onSave: () => void;
  readonly onSaveAs: () => void;
  readonly onDiscard: () => void;
}

export function ViewSaveBar(props: ViewSaveBarProps): React.JSX.Element | null {
  const { dirty, busy, canWrite, onSave, onSaveAs, onDiscard } = props;
  const t = useT();
  if (!dirty || !canWrite) return null;
  return (
    <div className="mesh-board__save-bar" role="status" data-testid="view-save-bar">
      <span className="mesh-board__save-hint">{t('board.unsavedChanges')}</span>
      <Button onClick={onSave} disabled={busy} data-testid="view-save">
        {t('board.saveChanges')}
      </Button>
      <Button variant="secondary" onClick={onSaveAs} disabled={busy} data-testid="view-save-as">
        {t('board.saveAs')}
      </Button>
      <Button variant="secondary" onClick={onDiscard} disabled={busy} data-testid="view-discard">
        {t('board.discardChanges')}
      </Button>
    </div>
  );
}
