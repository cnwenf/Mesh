/**
 * @提及解析纯函数测试(comment-inbox.md §4.1 / §4.3)。
 */
import { describe, expect, it } from 'vitest';
import {
  extractMentionedIds,
  filterCandidates,
  insertMention,
  parseMentionQuery,
  triggeredAgents,
} from '../mentions';
import type { MentionCandidate } from '../mentions';

const CANDIDATES: MentionCandidate[] = [
  { id: 'mem-1', name: 'Alice', member_type: 'human' },
  { id: 'mem-2', name: 'code-reviewer', member_type: 'agent' },
  { id: 'mem-3', name: 'Bob', member_type: 'human' },
];

describe('parseMentionQuery', () => {
  it('detects an @query at the cursor', () => {
    expect(parseMentionQuery('hello @al', 9)).toEqual({ start: 6, query: 'al' });
  });
  it('returns null when there is no @ or a space follows @', () => {
    expect(parseMentionQuery('hello world', 11)).toBeNull();
    expect(parseMentionQuery('hello @ world', 13)).toBeNull();
  });
  it('does not trigger mid-word (email-like)', () => {
    expect(parseMentionQuery('a@b', 3)).toBeNull();
  });
  it('triggers at start of input', () => {
    expect(parseMentionQuery('@', 1)).toEqual({ start: 0, query: '' });
  });
});

describe('insertMention', () => {
  it('replaces the @query with a mention chip + trailing space', () => {
    const next = insertMention('hello @al', 6, 9, CANDIDATES[0]);
    expect(next).toBe('hello [@Alice](mention://member/mem-1) ');
  });
});

describe('extractMentionedIds', () => {
  it('extracts unique ids in order', () => {
    const md = '[@A](mention://member/mem-1) [@B](mention://member/mem-2) [@A2](mention://member/mem-1)';
    expect(extractMentionedIds(md)).toEqual(['mem-1', 'mem-2']);
  });
  it('returns empty for no mentions', () => {
    expect(extractMentionedIds('plain text')).toEqual([]);
  });
});

describe('filterCandidates', () => {
  it('filters case-insensitively and returns all for empty query', () => {
    expect(filterCandidates(CANDIDATES, '').length).toBe(3);
    expect(filterCandidates(CANDIDATES, 'ali').map((c) => c.id)).toEqual(['mem-1']);
    expect(filterCandidates(CANDIDATES, 'CODE').map((c) => c.id)).toEqual(['mem-2']);
  });
});

describe('triggeredAgents', () => {
  it('returns only mentioned agents', () => {
    const md = '[@A](mention://member/mem-1) [@R](mention://member/mem-2)';
    expect(triggeredAgents(md, CANDIDATES).map((c) => c.id)).toEqual(['mem-2']);
  });
});
