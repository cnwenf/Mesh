/**
 * 面板文案组装与命中高亮(search-command-palette.md §3.2 / §6.18 / §6.12)。
 *
 * - 服务端不返回拼接好的可见句子:副标题由前端以 search.subtitle.* ICU 键 +
 *   结构化 context 本地化组装;徽章经 label_key + label_params 过消息目录;
 * - 高亮只消费 offset 区间(codepoint 单位,半开 [start,end)),经 Array.from(title)
 *   映射,绝不回显服务端 HTML(§5.3 注入防护);
 * - 命中以字重 + 下划线叠加,颜色不作唯一信号(§6.12);
 * - 字形图标为 ASCII 字符(emoji-free),aria-hidden 纯装饰。
 */
import type { TranslateFn } from '../../i18n';
import type { SearchBadge, SearchResultItem, SearchResultType } from './types';

/** 类型 → 装饰字形(无 emoji;屏幕阅读器经 aria-hidden 跳过) */
const GLYPH_BY_TYPE: Readonly<Record<SearchResultType | 'command' | 'favorite' | 'recent', string>> = {
  issue: '#',
  member: '@',
  agent: 'A',
  project: 'P',
  view: 'V',
  chat_session: 'C',
  command: '>',
  favorite: '*',
  recent: '~',
};

export function glyphFor(kind: SearchResultType | 'command' | 'favorite' | 'recent'): string {
  return GLYPH_BY_TYPE[kind];
}

/** 角色枚举本地化键(§3.2 结构化 context + 消息目录;裸枚举不得直接入句) */
const LOCALIZED_ROLES: readonly string[] = ['owner', 'admin', 'member', 'guest'];

/**
 * 成员/agent 角色本地化(§3.2):枚举值经 member.role.* 目录键渲染;未知角色
 * (目录外枚举)原样回退,避免呈现「member.role.xxx」死键。
 */
export function roleLabel(t: TranslateFn, role: string): string {
  return LOCALIZED_ROLES.includes(role) ? t(`member.role.${role}`) : role;
}

/**
 * 实体副标题(本地化组装,§3.2):枚举值经各自消息目录键
 * (project.visibility.* / view.scope.*),缺失项目名以空串占位(ICU 原样输出分隔符)。
 */
export function entitySubtitle(t: TranslateFn, item: SearchResultItem): string {
  switch (item.type) {
    case 'issue': {
      const { identifier, project, status } = item.context;
      return t('search.subtitle.issue', {
        identifier,
        project: project?.name ?? '',
        status: status.name,
      });
    }
    case 'member':
      return t('search.subtitle.member', { role: roleLabel(t, item.context.role) });
    case 'agent':
      return t('search.subtitle.agent', { role: roleLabel(t, item.context.role) });
    case 'project':
      return t('search.subtitle.project', {
        key: item.context.key,
        visibility: t(`project.visibility.${item.context.visibility}`),
      });
    case 'view':
      return t('search.subtitle.view', { scope: t(`view.scope.${item.context.scope}`) });
    case 'chat_session':
      return t('search.subtitle.chat', { agent: item.context.agent?.name ?? '' });
  }
}

/** agent 容量呈现(§6.12「运行中 N / 排队 M / 需审批 K」;context.capacity 存在时才展示) */
export function agentCapacityText(t: TranslateFn, item: SearchResultItem): string | null {
  if (item.type !== 'agent') return null;
  const capacity = item.context.capacity;
  if (capacity === undefined) return null;
  return t('search.capacity', {
    running: capacity.running,
    queued: capacity.queued,
    awaiting: capacity.awaiting_approval,
  });
}

/** 徽章可见文案 = 消息目录 key + 参数(§3.2) */
export function badgeText(t: TranslateFn, badge: SearchBadge): string {
  return t(badge.label_key, badge.label_params);
}

/** 高亮渲染片段:hit=true 的片段以字重 + 下划线叠加呈现(非颜色唯一信号,§6.12) */
export interface HighlightSpan {
  readonly text: string;
  readonly hit: boolean;
}

function normalizeRanges(
  ranges: ReadonlyArray<readonly [number, number]> | undefined,
  length: number,
): ReadonlyArray<readonly [number, number]> {
  if (ranges === undefined) return [];
  const clamped: Array<[number, number]> = [];
  for (const range of ranges) {
    const start = range[0];
    const end = range[1];
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
    const from = Math.max(0, Math.min(Math.trunc(start), length));
    const to = Math.max(0, Math.min(Math.trunc(end), length));
    if (to > from) clamped.push([from, to]);
  }
  // 合并重叠/相邻区间,简化渲染分段
  clamped.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const [from, to] of clamped) {
    const last = merged[merged.length - 1];
    if (last !== undefined && from <= last[1]) {
      last[1] = Math.max(last[1], to);
    } else {
      merged.push([from, to]);
    }
  }
  return merged;
}

/**
 * 将原始 title 按 codepoint 区间切分为命中/非命中片段。
 * offset 基于 Array.from(title)(codepoint 序列,§3.2),区间外的非法/越界值被钳制或忽略。
 * 无区间 → 单一非命中片段;空 title → 空数组。
 */
export function splitHighlight(
  title: string,
  ranges: ReadonlyArray<readonly [number, number]> | undefined,
): readonly HighlightSpan[] {
  const codepoints = Array.from(title);
  if (codepoints.length === 0) return [];
  const merged = normalizeRanges(ranges, codepoints.length);
  if (merged.length === 0) {
    return [{ text: title, hit: false }];
  }
  const spans: HighlightSpan[] = [];
  let cursor = 0;
  for (const [from, to] of merged) {
    if (from > cursor) {
      spans.push({ text: codepoints.slice(cursor, from).join(''), hit: false });
    }
    spans.push({ text: codepoints.slice(from, to).join(''), hit: true });
    cursor = to;
  }
  if (cursor < codepoints.length) {
    spans.push({ text: codepoints.slice(cursor).join(''), hit: false });
  }
  return spans;
}
