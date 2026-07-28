/**
 * buildDistillBody 测试(§4 沉淀正文汇编):角色标注、分隔线、系统消息过滤、空列表。
 */
import { describe, expect, it } from 'vitest';
import { buildDistillBody } from '../distill';
import type { ChatMessage } from '../types';

function makeMessage(role: ChatMessage['role'], content: string): ChatMessage {
  return {
    id: 'm', session_id: 's', role, content, generation_id: null, generation_status: 'done',
    parent_id: null, selected_candidate: true, quote_message_id: null, prompt_tokens: null,
    completion_tokens: null, error_message: null, started_at: null, finished_at: null,
    created_at: '2026-07-01T00:00:00Z', attachments: [], candidate_count: null, candidate_index: null,
  };
}

describe('buildDistillBody(§4 沉淀正文)', () => {
  it('逐条消息标注角色并以分隔线隔断', () => {
    const body = buildDistillBody(
      [makeMessage('user', 'question'), makeMessage('agent', 'answer')],
      'You',
      'Agent',
    );
    expect(body).toBe('**You**\n\nquestion\n\n---\n\n**Agent**\n\nanswer');
  });

  it('过滤系统消息', () => {
    const body = buildDistillBody(
      [makeMessage('user', 'q'), makeMessage('system', 'noise'), makeMessage('agent', 'a')],
      'You',
      'Agent',
    );
    expect(body).not.toContain('noise');
    expect(body).toContain('q');
    expect(body).toContain('a');
  });

  it('空列表返回空串', () => {
    expect(buildDistillBody([], 'You', 'Agent')).toBe('');
  });
});
