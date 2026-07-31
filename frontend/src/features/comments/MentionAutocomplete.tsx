/**
 * @提及补全弹层(comment-inbox.md §4.1):人/agent 混排,agent 项视觉区分(「Agent」徽标)
 * 并附副作用提示「发布后将触发一次运行」(README §6.9,措辞不得暗示选中即触发)。
 * 键盘:↑/↓ 移动高亮,Enter 选择,Esc 关闭(由 composer 转发 keydown)。
 * 纯展示 + 回调。
 */
import { Icon } from '../../design';
import { useT } from '../../i18n';
import type { MentionCandidate } from './mentions';

export interface MentionAutocompleteProps {
  readonly candidates: readonly MentionCandidate[];
  readonly activeIndex: number;
  readonly onSelect: (member: MentionCandidate) => void;
  readonly onHover: (index: number) => void;
}

export function MentionAutocomplete(props: MentionAutocompleteProps): React.JSX.Element {
  const t = useT();
  const { candidates, activeIndex } = props;
  return (
    <ul className="mesh-comments__mention-list" role="listbox" data-testid="mention-list">
      {candidates.map((member, index) => {
        const isAgent = member.member_type === 'agent';
        return (
          <li
            key={member.id}
            role="option"
            aria-selected={index === activeIndex}
            className={
              index === activeIndex
                ? 'mesh-comments__mention-item mesh-comments__mention-item--active'
                : 'mesh-comments__mention-item'
            }
            data-testid={`mention-item-${member.id}`}
            onMouseEnter={() => props.onHover(index)}
            onMouseDown={(event) => {
              // preventDefault 防止 textarea 失焦导致光标丢失
              event.preventDefault();
              props.onSelect(member);
            }}
          >
            <span className="mesh-comments__mention-name">{member.name}</span>
            {isAgent ? (
              <span className="mesh-comments__badge mesh-comments__badge--agent">
                {t('comments.badge.agent')}
              </span>
            ) : null}
            {isAgent ? (
              /* @agent 候选显示「发布后将触发一次运行」(§9.5.2 / parity §2.10):
                 sparkle 图标 + 徽标双信号,措辞不暗示选中即触发。 */
              <span className="mesh-comments__mention-run" data-testid="mention-agent-hint">
                <Icon name="sparkle" size={16} className="mesh-comments__mention-run-icon" />
                <span className="mesh-comments__badge mesh-comments__badge--run">
                  {t('comments.mentionWillRun')}
                </span>
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
