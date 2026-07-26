/**
 * 评论类型守卫测试(comment-inbox.md §2.2)。
 */
import { describe, expect, it } from 'vitest';
import type { Comment } from '../types';
import { isComment, isCommentMemberRef, isDeletedComment, isResolved } from '../types';

const BASE: Comment = {
  id: 'c-1',
  issue_id: 'iss-1',
  parent_id: null,
  thread_root_id: null,
  author_kind: 'member',
  author: { id: 'mem-1', member_type: 'human', name: 'Owner' },
  body_markdown: 'hi',
  body_html: '<p>hi</p>',
  body_text: 'hi',
  reactions: [],
  reply_count: 0,
  resolved_at: null,
  resolved_by: null,
  mentions: [],
  triggered_execution_ids: [],
  deleted_at: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  edited_at: null,
};

describe('isCommentMemberRef', () => {
  it('accepts a well-formed ref', () => {
    expect(isCommentMemberRef({ id: 'm', member_type: 'agent', name: 'bot' })).toBe(true);
  });
  it('rejects malformed values', () => {
    expect(isCommentMemberRef(null)).toBe(false);
    expect(isCommentMemberRef({ id: 'm', name: 'x' })).toBe(false);
    expect(isCommentMemberRef({ id: 'm', member_type: 'robot', name: 'x' })).toBe(false);
  });
});

describe('isComment', () => {
  it('accepts a comment-shaped payload', () => {
    expect(isComment(BASE)).toBe(true);
  });
  it('rejects non-objects and missing fields', () => {
    expect(isComment(null)).toBe(false);
    expect(isComment({ id: 'c-1' })).toBe(false);
    expect(isComment({ id: 'c-1', issue_id: 'i', created_at: 'x' })).toBe(false);
  });
});

it('isDeletedComment / isResolved reflect timestamps', () => {
  expect(isDeletedComment(BASE)).toBe(false);
  expect(isResolved(BASE)).toBe(false);
  expect(isDeletedComment({ ...BASE, deleted_at: '2026-07-02T00:00:00Z' })).toBe(true);
  expect(isResolved({ ...BASE, resolved_at: '2026-07-02T00:00:00Z' })).toBe(true);
});
