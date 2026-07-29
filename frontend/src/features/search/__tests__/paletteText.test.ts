/**
 * 文案组装与高亮单测(§3.2 / §6.18 / §6.12)。
 */
import { describe, expect, it } from 'vitest';
import type { TranslateFn } from '../../../i18n';
import { agentCapacityText, badgeText, entitySubtitle, glyphFor, splitHighlight } from '../paletteText';
import type { SearchResultItem } from '../types';

/** 记录 key + 参数的桩 t:断言本地化组装经消息目录而非拼接句子 */
function recordingT(calls: Array<{ key: string; values?: Record<string, unknown> }>): TranslateFn {
  return (key, values) => {
    calls.push({ key, values: values as Record<string, unknown> | undefined });
    return key;
  };
}

describe('splitHighlight(codepoint 区间映射,§3.2)', () => {
  it('无区间 → 单一非命中片段', () => {
    expect(splitHighlight('hello', undefined)).toEqual([{ text: 'hello', hit: false }]);
    expect(splitHighlight('hello', [])).toEqual([{ text: 'hello', hit: false }]);
  });

  it('区间 [0,2) 切出命中前缀', () => {
    expect(splitHighlight('hello', [[0, 2]])).toEqual([
      { text: 'he', hit: true },
      { text: 'llo', hit: false },
    ]);
  });

  it('offset 按 Unicode code point 计(多字节字符精确对齐)', () => {
    // '登录页在 Safari 崩溃' 的前两个 code point 为 '登录'
    const spans = splitHighlight('登录页在 Safari 崩溃', [[0, 2]]);
    expect(spans[0]).toEqual({ text: '登录', hit: true });
    expect(spans[1]?.hit).toBe(false);
  });

  it('代理对(emoji)按单 code point 处理', () => {
    const spans = splitHighlight('A💩B', [[1, 2]]);
    expect(spans).toEqual([
      { text: 'A', hit: false },
      { text: '💩', hit: true },
      { text: 'B', hit: false },
    ]);
  });

  it('重叠区间合并;越界钳制;非法区间忽略', () => {
    expect(splitHighlight('abcdef', [[0, 3], [2, 4]])).toEqual([
      { text: 'abcd', hit: true },
      { text: 'ef', hit: false },
    ]);
    expect(splitHighlight('abc', [[1, 99]])).toEqual([
      { text: 'a', hit: false },
      { text: 'bc', hit: true },
    ]);
    expect(splitHighlight('abc', [[3, 1], [Number.NaN, 2]])).toEqual([{ text: 'abc', hit: false }]);
  });

  it('空 title → 空数组', () => {
    expect(splitHighlight('', [[0, 1]])).toEqual([]);
  });
});

describe('entitySubtitle(本地化副标题组装,§3.2/§6.18)', () => {
  it('issue:identifier · project · status', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    const item: SearchResultItem = {
      type: 'issue',
      id: 'i1',
      title: 't',
      context: {
        identifier: 'WEB-124',
        project: { id: 'p1', name: 'Website' },
        status: { id: 's1', name: 'In Progress', category: 'in_progress' },
      },
      icon: 'issue',
      url: '/u',
    };
    entitySubtitle(recordingT(calls), item);
    expect(calls).toEqual([
      {
        key: 'search.subtitle.issue',
        values: { identifier: 'WEB-124', project: 'Website', status: 'In Progress' },
      },
    ]);
  });

  it('issue 无项目 → project 以空串占位', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    const item: SearchResultItem = {
      type: 'issue',
      id: 'i1',
      title: 't',
      context: { identifier: 'WEB-1', project: null, status: { id: 's', name: 'Todo', category: 'todo' } },
      icon: 'issue',
      url: '/u',
    };
    entitySubtitle(recordingT(calls), item);
    expect(calls[0]?.values).toMatchObject({ project: '' });
  });

  it('project:visibility 枚举经消息目录键', () => {
    const calls: Array<{ key: string }> = [];
    const item: SearchResultItem = {
      type: 'project',
      id: 'p1',
      title: 't',
      context: { visibility: 'private', key: 'WEB' },
      icon: 'project',
      url: '/u',
    };
    entitySubtitle(recordingT(calls), item);
    expect(calls.map((call) => call.key)).toEqual(['project.visibility.private', 'search.subtitle.project']);
  });

  it('view:scope 枚举经消息目录键', () => {
    const calls: Array<{ key: string }> = [];
    const item: SearchResultItem = {
      type: 'view',
      id: 'v1',
      title: 't',
      context: { scope: 'workspace' },
      icon: 'view',
      url: '/u',
    };
    entitySubtitle(recordingT(calls), item);
    expect(calls.map((call) => call.key)).toEqual(['view.scope.workspace', 'search.subtitle.view']);
  });

  it('member / agent 角色经 member.role.* 本地化(不裸插枚举,MES-79 M9)', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    const t = recordingT(calls);
    entitySubtitle(t, {
      type: 'member',
      id: 'm1',
      title: 't',
      context: { member_type: 'human', role: 'admin' },
      icon: 'member',
      url: '/u',
    });
    entitySubtitle(t, {
      type: 'agent',
      id: 'a1',
      title: 't',
      context: { member_type: 'agent', role: 'member' },
      icon: 'agent',
      url: '/u',
    });
    // 角色先经 member.role.* 目录键本地化,再以本地化结果入副标题 ICU。
    expect(calls.map((call) => call.key)).toEqual([
      'member.role.admin',
      'search.subtitle.member',
      'member.role.member',
      'search.subtitle.agent',
    ]);
    // recordingT 返回键本身 → 副标题 role 参数为本地化键(真实目录则渲染「管理员」等)。
    expect(calls[1]?.values).toEqual({ role: 'member.role.admin' });
  });

  it('未知角色(目录外枚举)原样回退,不呈现 member.role.xxx 死键(M9)', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    entitySubtitle(recordingT(calls), {
      type: 'member',
      id: 'm1',
      title: 't',
      context: { member_type: 'human', role: 'superuser' },
      icon: 'member',
      url: '/u',
    });
    expect(calls.map((call) => call.key)).toEqual(['search.subtitle.member']);
    expect(calls[0]?.values).toEqual({ role: 'superuser' });
  });

  it('chat_session 走自身键', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    entitySubtitle(recordingT(calls), {
      type: 'chat_session',
      id: 'c1',
      title: 't',
      context: { participants_count: 2, agent: { id: 'a1', name: 'Helper' } },
      icon: 'chat_session',
      url: '/u',
    });
    expect(calls.map((call) => call.key)).toEqual(['search.subtitle.chat']);
    expect(calls[0]?.values).toEqual({ agent: 'Helper' });
  });

  it('chat_session 无 agent → agent 参数空串', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    entitySubtitle(recordingT(calls), {
      type: 'chat_session',
      id: 'c1',
      title: 't',
      context: { participants_count: 2 },
      icon: 'chat_session',
      url: '/u',
    });
    expect(calls[0]?.values).toEqual({ agent: '' });
  });
});

describe('agentCapacityText(§6.12 容量呈现)', () => {
  it('agent 有 capacity → search.capacity 键 + 三参数', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    const text = agentCapacityText(recordingT(calls), {
      type: 'agent',
      id: 'a1',
      title: 't',
      context: {
        member_type: 'agent',
        role: 'member',
        capacity: { running: 2, queued: 1, awaiting_approval: 3 },
      },
      icon: 'agent',
      url: '/u',
    });
    expect(text).toBe('search.capacity');
    expect(calls[0]?.values).toEqual({ running: 2, queued: 1, awaiting: 3 });
  });

  it('agent 无 capacity / 非 agent → null', () => {
    const t = recordingT([]);
    expect(
      agentCapacityText(t, {
        type: 'agent',
        id: 'a1',
        title: 't',
        context: { member_type: 'agent', role: 'member' },
        icon: 'agent',
        url: '/u',
      }),
    ).toBeNull();
    expect(
      agentCapacityText(t, {
        type: 'member',
        id: 'm1',
        title: 't',
        context: { member_type: 'human', role: 'member' },
        icon: 'member',
        url: '/u',
      }),
    ).toBeNull();
  });
});

describe('badgeText / glyphFor', () => {
  it('徽章文案 = 消息目录 key + 参数', () => {
    const calls: Array<{ key: string; values?: Record<string, unknown> }> = [];
    badgeText(recordingT(calls), {
      kind: 'status',
      label_key: 'issue.status.name',
      label_params: { name: 'In Progress' },
      color: 'info',
    });
    expect(calls).toEqual([{ key: 'issue.status.name', values: { name: 'In Progress' } }]);
  });

  it('类型为 ASCII 字形映射(无 emoji)', () => {
    expect(glyphFor('issue')).toBe('#');
    expect(glyphFor('command')).toBe('>');
    // eslint-disable-next-line no-control-regex -- 断言纯 ASCII(emoji-free)
    expect(glyphFor('agent')).toMatch(/^[\x00-\x7F]$/);
  });
});
