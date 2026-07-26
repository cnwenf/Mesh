/**
 * 评论表情回应区(comment-inbox.md §4.1 第 4 点):一排 emoji chip(emoji + 计数),
 * 已反应高亮;「+」打开迷你选择器。点击 chip 增/减自己的反应(由父级乐观处理)。
 * 纯展示 + 回调;无数据获取。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:候选 emoji 常量与组件同文件共存 */
import { useState } from 'react';
import { useT } from '../../i18n';
import type { ReactionSummary } from './types';

/** 迷你选择器候选 emoji(轻量内置集,够用即可)。 */
export const REACTION_PALETTE: readonly string[] = ['👍', '🎉', '❤️', '🚀', '👀', '✅'];

export interface ReactionBarProps {
  readonly reactions: readonly ReactionSummary[];
  /** 点击已有 chip:reacted_by_me ? 取消 : 添加。 */
  readonly onToggle: (emoji: string) => void;
  /** 从选择器新增一个 emoji。 */
  readonly onAdd: (emoji: string) => void;
}

export function ReactionBar(props: ReactionBarProps): React.JSX.Element {
  const t = useT();
  const [pickerOpen, setPickerOpen] = useState(false);
  const { reactions } = props;

  return (
    <div className="mesh-comments__reactions">
      {reactions.map((reaction) => (
        <button
          key={reaction.emoji}
          type="button"
          className={
            reaction.reacted_by_me
              ? 'mesh-comments__reaction mesh-comments__reaction--mine'
              : 'mesh-comments__reaction'
          }
          data-testid={`reaction-${reaction.emoji}`}
          aria-pressed={reaction.reacted_by_me}
          title={reaction.actors.map((actor) => actor.name).join(', ')}
          onClick={() => props.onToggle(reaction.emoji)}
        >
          <span aria-hidden="true">{reaction.emoji}</span> {reaction.count}
        </button>
      ))}
      <button
        type="button"
        className="mesh-comments__reaction-add"
        data-testid="reaction-add"
        aria-label={t('comments.reaction.add')}
        aria-expanded={pickerOpen}
        onClick={() => setPickerOpen((open) => !open)}
      >
        +
      </button>
      {pickerOpen ? (
        <div className="mesh-comments__reaction-picker" role="menu" data-testid="reaction-picker">
          {REACTION_PALETTE.map((emoji) => (
            <button
              key={emoji}
              type="button"
              role="menuitem"
              data-testid={`reaction-pick-${emoji}`}
              onClick={() => {
                props.onAdd(emoji);
                setPickerOpen(false);
              }}
            >
              {emoji}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
