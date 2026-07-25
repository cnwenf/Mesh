/**
 * 占位页(收件箱/项目/看板/成员/聊天/自动化):空态呈现,真实功能归阶段 2 各模块。
 * 标题取 nav.<kind>,描述取 state.emptyDescription(异常态矩阵 empty 行)。
 */
import { EmptyState } from '../design';
import { useT } from '../i18n';

export type PlaceholderKind = 'inbox' | 'projects' | 'board' | 'members' | 'chat' | 'automation';

export interface PlaceholderPageProps {
  kind: PlaceholderKind;
}

export function PlaceholderPage(props: PlaceholderPageProps): React.JSX.Element {
  const t = useT();
  const { kind } = props;
  return (
    <div className="mesh-placeholder">
      <EmptyState title={t('nav.' + kind)} description={t('state.emptyDescription')} />
    </div>
  );
}
