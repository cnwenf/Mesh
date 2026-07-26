/**
 * @提及解析纯函数(comment-inbox.md §4.1 / §4.3)。
 * 提及语法:`[@Name](mention://member/<uuid>)`(chip)+ 纯文本 `@Name`;
 * 服务端对最终 Markdown 做权威解析,客户端仅负责输入辅助与触发预览的本地估算。
 */

/** 提及候选:人/agent 混排,member_type 供 UI 区分与触发预览。 */
export interface MentionCandidate {
  readonly id: string;
  readonly name: string;
  readonly member_type: 'human' | 'agent';
}

export interface MentionQuery {
  /** 命中 `@` 的起点下标(含 `@`)。 */
  readonly start: number;
  /** `@` 之后到光标的查询串(可能为空)。 */
  readonly query: string;
}

/** 提及链接引用:捕获 `mention://member/<id>` 中的 id(至 `)`/空白前,兼容 UUID 与测试 id)。 */
const MENTION_LINK_PATTERN = /mention:\/\/member\/([^)\s]+)/g;

/**
 * 在 value 的 cursor 处向前扫描最近的 `@` 触发词;@ 与光标间不得有空白/换行,
 * 否则视为非提及上下文(返回 null)。
 */
export function parseMentionQuery(value: string, cursor: number): MentionQuery | null {
  const upto = value.slice(0, cursor);
  const at = upto.lastIndexOf('@');
  if (at === -1) return null;
  const between = upto.slice(at + 1);
  // @ 之后出现空白/换行 → 不是正在输入的提及
  if (/\s/.test(between)) return null;
  // @ 前一字符若为单词字符(如 email a@b)则不触发
  const prev = at > 0 ? upto[at - 1] : ' ';
  if (prev !== ' ' && prev !== '\n' && at !== 0) return null;
  return { start: at, query: between };
}

/** 用提及 chip 替换 value 中 [start, cursor) 的 `@query`,尾随一个空格。 */
export function insertMention(
  value: string,
  start: number,
  cursor: number,
  member: MentionCandidate,
): string {
  const chip = `[@${member.name}](mention://member/${member.id})`;
  return value.slice(0, start) + chip + ' ' + value.slice(cursor);
}

/** 提取 Markdown 中所有 mention 链接引用的成员 id(去重,保序)。 */
export function extractMentionedIds(markdown: string): readonly string[] {
  const ids: string[] = [];
  for (const match of markdown.matchAll(MENTION_LINK_PATTERN)) {
    const id = match[1];
    if (id !== undefined && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

/** 按查询串过滤候选(大小写不敏感子串;空查询返回全部)。 */
export function filterCandidates(
  candidates: readonly MentionCandidate[],
  query: string,
): readonly MentionCandidate[] {
  const needle = query.trim().toLowerCase();
  if (needle === '') return candidates;
  return candidates.filter((candidate) => candidate.name.toLowerCase().includes(needle));
}

/** 估算将被触发的 agent 清单(被提及且未抑制;README §6.9 触发预览)。 */
export function triggeredAgents(
  markdown: string,
  candidates: readonly MentionCandidate[],
): readonly MentionCandidate[] {
  const ids = extractMentionedIds(markdown);
  return candidates.filter(
    (candidate) => candidate.member_type === 'agent' && ids.includes(candidate.id),
  );
}
