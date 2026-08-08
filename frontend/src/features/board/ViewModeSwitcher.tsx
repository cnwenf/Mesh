/**
 * 视图模式切换器(对齐看板页三视图直切:看板 ↔ 泳道 ↔ 列表)。
 *
 * 模式是视图配置(layout + sub_group_by)的派生呈现:
 * - board    = layout 'board' 且无二级分组(单维状态列);
 * - swimlane = layout 'board' 且有 sub_group_by(二维泳道);
 * - list     = layout 'list'。
 * 切换即把对应配置写回视图(持久化),筛选/排序/一级分组保持不变,
 * 因此三视图互切时「状态」(过滤条件等)得以保留。
 */
/* eslint-disable react-refresh/only-export-components -- deriveViewMode 与切换器同语义,共置便于测试 */
import { Button, Icon } from '../../design';
import type { IconName } from '../../design';
import { useT } from '../../i18n';
import type { View } from './types';

export type ViewMode = 'board' | 'swimlane' | 'list';

const MODE_ICONS: Record<ViewMode, IconName> = {
  board: 'board',
  swimlane: 'menu',
  list: 'list',
};

const MODE_ORDER: readonly ViewMode[] = ['board', 'swimlane', 'list'];

/** 由视图配置派生当前视图模式(纯函数,便于单测)。 */
export function deriveViewMode(view: Pick<View, 'layout' | 'sub_group_by'>): ViewMode {
  if (view.layout === 'list') return 'list';
  if (view.layout === 'board' && view.sub_group_by !== null) return 'swimlane';
  return 'board';
}

interface ViewModeSwitcherProps {
  readonly value: ViewMode;
  readonly disabled?: boolean;
  readonly onChange: (mode: ViewMode) => void;
}

export function ViewModeSwitcher(props: ViewModeSwitcherProps): React.JSX.Element {
  const { value, disabled = false, onChange } = props;
  const t = useT();

  return (
    <div
      className="mesh-view-mode"
      role="group"
      aria-label={t('board.viewModeLabel')}
      data-testid="view-mode-switcher"
    >
      {MODE_ORDER.map((mode) => {
        const active = mode === value;
        return (
          <Button
            key={mode}
            variant={active ? 'primary' : 'ghost'}
            size="sm"
            disabled={disabled}
            aria-pressed={active}
            className={
              active ? 'mesh-view-mode__btn mesh-view-mode__btn--active' : 'mesh-view-mode__btn'
            }
            data-testid={`view-mode-${mode}`}
            onClick={() => {
              if (!active) onChange(mode);
            }}
          >
            <Icon name={MODE_ICONS[mode]} size={16} />
            {t('board.viewMode.' + mode)}
          </Button>
        );
      })}
    </div>
  );
}
