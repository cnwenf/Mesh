/**
 * 补充覆盖(comment-inbox §4):useCommentsData 纯函数的全分支 + MentionAutocomplete
 * 鼠标交互(onHover/onSelect)。纯函数分支与交互分支补齐,避免被全局分支门禁掩盖。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { MentionAutocomplete } from '../MentionAutocomplete';
import { applyCommentsFrame } from '../realtime';
import { patchCommentById, toggleReactionLocal } from '../useCommentsData';
import type { Comment, CommentMemberRef, ReactionSummary } from '../types';
import type { RealtimeEventFrame } from '../../../types/realtime';

function frame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'issue:iss-1', seq: 1, event, payload } as RealtimeEventFrame;
}

const ME: CommentMemberRef = { id: 'me', member_type: 'human', name: 'Me' };
const OTHER: CommentMemberRef = { id: 'other', member_type: 'human', name: 'Other' };

function comment(id: string, previewReplies?: Comment[]): Comment {
  return {
    id,
    issue_id: 'i-1',
    parent_id: null,
    thread_root_id: null,
    author_kind: 'member',
    author: ME,
    body_markdown: 'x',
    body_html: '<p>x</p>',
    body_text: 'x',
    reactions: [],
    reply_count: previewReplies ? previewReplies.length : 0,
    resolved_at: null,
    resolved_by: null,
    mentions: [],
    triggered_execution_ids: [],
    deleted_at: null,
    edited_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    preview_replies: previewReplies,
  };
}

describe('patchCommentById 全分支', () => {
  it('顶层命中 → patch', () => {
    const out = patchCommentById([comment('a')], 'a', (c) => ({ ...c, body_text: 'y' }));
    expect(out[0].body_text).toBe('y');
  });

  it('未命中且无 preview_replies → 原引用', () => {
    const list = [comment('a')];
    const out = patchCommentById(list, 'zzz', (c) => ({ ...c, body_text: 'y' }));
    expect(out).toBe(list);
  });

  it('嵌套 reply 命中 → patch 内层', () => {
    const out = patchCommentById(
      [comment('root', [comment('r1')])],
      'r1',
      (c) => ({ ...c, body_text: 'nested-y' }),
    );
    expect(out[0].preview_replies?.[0].body_text).toBe('nested-y');
  });

  it('有 preview_replies 但 id 不匹配 → 原引用', () => {
    const list = [comment('root', [comment('r1')])];
    const out = patchCommentById(list, 'nope', (c) => ({ ...c, body_text: 'y' }));
    expect(out).toBe(list);
  });
});

describe('toggleReactionLocal 全分支', () => {
  it('无该 emoji → 新增一条 reacted_by_me', () => {
    const out = toggleReactionLocal([], '👍', ME);
    expect(out).toEqual([{ emoji: '👍', count: 1, reacted_by_me: true, actors: [ME] }]);
  });

  it('自己已反应且 count 降到 0 → 移除该 emoji', () => {
    const reactions: ReactionSummary[] = [{ emoji: '👍', count: 1, reacted_by_me: true, actors: [ME] }];
    const out = toggleReactionLocal(reactions, '👍', ME);
    expect(out).toEqual([]);
  });

  it('自己已反应但仍有他人 → count-1 且 reacted_by_me=false', () => {
    const reactions: ReactionSummary[] = [
      { emoji: '👍', count: 2, reacted_by_me: true, actors: [ME, OTHER] },
    ];
    const out = toggleReactionLocal(reactions, '👍', ME);
    expect(out[0]).toMatchObject({ emoji: '👍', count: 1, reacted_by_me: false });
    expect(out[0].actors).toEqual([OTHER]);
  });

  it('他人已反应自己未反应 → 加入自己 count+1', () => {
    const reactions: ReactionSummary[] = [{ emoji: '🎉', count: 1, reacted_by_me: false, actors: [OTHER] }];
    const out = toggleReactionLocal(reactions, '🎉', ME);
    expect(out[0]).toMatchObject({ emoji: '🎉', count: 2, reacted_by_me: true });
    expect(out[0].actors).toEqual([OTHER, ME]);
  });
});

describe('MentionAutocomplete 鼠标交互', () => {
  const candidates = [
    { id: 'mem-h', name: 'Human', member_type: 'human' as const },
    { id: 'mem-a', name: 'agent', member_type: 'agent' as const },
  ];

  it('onMouseEnter 触发 onHover,onMouseDown 触发 onSelect 并 preventDefault', () => {
    const onHover = vi.fn();
    const onSelect = vi.fn();
    renderWithProviders(
      <MentionAutocomplete
        candidates={candidates}
        activeIndex={0}
        onHover={onHover}
        onSelect={onSelect}
      />,
    );
    fireEvent.mouseEnter(screen.getByTestId('mention-item-mem-h'));
    expect(onHover).toHaveBeenCalledWith(0);
    const prevented = fireEvent.mouseDown(screen.getByTestId('mention-item-mem-a'));
    expect(prevented).toBe(false); // preventDefault 被调用
    expect(onSelect).toHaveBeenCalledWith(candidates[1]);
  });

  it('agent 项渲染 Agent 徽标与副作用提示,human 项不渲染', () => {
    renderWithProviders(
      <MentionAutocomplete
        candidates={candidates}
        activeIndex={1}
        onHover={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByTestId('mention-agent-hint')).toBeTruthy();
    expect(screen.getAllByTestId('mention-agent-hint')).toHaveLength(1);
  });
});

describe('applyCommentsFrame replaceById 嵌套分支', () => {
  it('comment.updated 命中嵌套 reply(顶层 id 不同)→ 走 nested 分支', () => {
    const reply = comment('r-1');
    const root = { ...comment('root'), preview_replies: [reply], reply_count: 1 };
    const next = applyCommentsFrame(
      [root],
      frame('comment.updated', { id: 'r-1', body_text: 'nested-edited', updated_at: '2026-07-03T00:00:00Z' }),
    );
    expect(next[0].preview_replies?.[0].body_text).toBe('nested-edited');
  });

  it('comment.deleted 命中嵌套 reply → 走 nested 分支清空 body', () => {
    const reply = comment('r-2');
    const root = { ...comment('root2'), preview_replies: [reply], reply_count: 1 };
    const next = applyCommentsFrame(
      [root],
      frame('comment.deleted', { id: 'r-2', deleted_at: '2026-07-04T00:00:00Z' }),
    );
    expect(next[0].preview_replies?.[0].deleted_at).toBe('2026-07-04T00:00:00Z');
    expect(next[0].preview_replies?.[0].body_markdown).toBe('');
  });
});
