/**
 * locale 协商链 — README §6.18(唯一权威)/ i18n.md §3.3。
 *
 * 优先级(高 → 低):
 * 1. 请求显式参数(`?locale=` / `Accept-Language`,二者同属一级,显式参数更明确)
 * 2. 用户偏好 `users.settings.locale`(为 null 则跳过本级)
 * 3. 工作区默认 `workspaces.settings.default_locale`(§2.3 唯一工作区 locale 真源)
 * 4. 系统回退 `en`
 *
 * 协商是尽力而为:非法/未知 locale 值一律忽略并继续,绝不抛错、绝不返回 400。
 */

export const SUPPORTED_LOCALES = ['zh-CN', 'en'] as const;

export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

export const FALLBACK_LOCALE = 'en';

/**
 * BCP-47 语法的轻量校验(子标签:1-8 位字母数字,首标签必须含字母)。
 * 仅用于 Accept-Language 解析时剔除明显垃圾;正式的 BCP-47 校验见 format.ts 的
 * isValidLocaleTag(Intl.Locale)。此处刻意保持零依赖、零分配、绝不抛错。
 */
const BCP47_SYNTAX = /^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$/;

const MAX_Q_VALUE = 1;
const MIN_Q_VALUE = 0;

interface RankedCandidate {
  readonly tag: string;
  readonly q: number;
  readonly order: number;
}

/**
 * 解析 Accept-Language 头为候选 locale 序列(q 值降序;同 q 保持出现顺序)。
 * - 非法标签(非 BCP-47 语法)、非法 q 值、q=0(RFC 9110「不接受」)与 `*` 一律剔除;
 * - 非字符串 / 空输入返回空数组,绝不抛错。
 */
export function parseAcceptLanguage(header: string): string[] {
  if (typeof header !== 'string' || header.length === 0) return [];
  const ranked: RankedCandidate[] = [];
  let order = 0;
  for (const rawEntry of header.split(',')) {
    const entry = rawEntry.trim();
    if (entry.length === 0) continue;
    const segments = entry.split(';').map((segment) => segment.trim());
    const tag = segments[0];
    if (tag === '*' || !BCP47_SYNTAX.test(tag)) continue;
    const q = parseQuality(segments.slice(1));
    if (q === null || q <= MIN_Q_VALUE) continue;
    ranked.push({ tag, q, order });
    order += 1;
  }
  return ranked.sort((a, b) => b.q - a.q || a.order - b.order).map((candidate) => candidate.tag);
}

/** 解析 q 参数;非法值返回 null(调用方据此剔除该候选)。缺省 q = 1。 */
function parseQuality(params: readonly string[]): number | null {
  let q = MAX_Q_VALUE;
  for (const param of params) {
    const match = /^q=(.*)$/i.exec(param);
    if (match === null) continue;
    const value = Number(match[1]);
    if (!Number.isFinite(value) || value < MIN_Q_VALUE || value > MAX_Q_VALUE) return null;
    q = value;
  }
  return q;
}

/**
 * 在受支持清单中匹配候选(§3.3 BCP-47 匹配):
 * 逐候选处理 —— 先精确匹配(大小写不敏感);不中则按语言主干回退
 * (如 `zh-TW` → 同语言受支持区域变体 `zh-CN`;纯主干 `zh` 亦命中 `zh-CN`);
 * 仍不中则取下一候选;全部不中返回 null。
 */
export function matchSupported(
  requested: readonly string[],
  supported: readonly string[],
): string | null {
  if (!Array.isArray(requested) || !Array.isArray(supported)) return null;
  const supportedLower = supported.map((locale) => locale.toLowerCase());
  for (const candidate of requested) {
    if (typeof candidate !== 'string') continue;
    const normalized = candidate.trim().toLowerCase();
    if (normalized.length === 0) continue;
    const exactIndex = supportedLower.indexOf(normalized);
    if (exactIndex !== -1) return supported[exactIndex];
    const trunk = normalized.split('-')[0];
    const trunkIndex = supportedLower.findIndex((locale) => locale.split('-')[0] === trunk);
    if (trunkIndex !== -1) return supported[trunkIndex];
  }
  return null;
}

export interface NegotiateLocaleInput {
  /** 请求显式参数:?locale= 单值或 Accept-Language 头(字符串),或已解析候选序列 */
  readonly requested?: string | readonly string[] | null;
  /** users.settings.locale;null → 跳过本级 */
  readonly userLocale?: string | null;
  /** workspaces.settings.default_locale;null → 跳过本级 */
  readonly workspaceDefaultLocale?: string | null;
  /**
   * 系统级候选(浏览器 navigator.languages,Accept-Language 的 SPA 等价物);
   * 位于工作区默认之后、fallback 之前 —— 账号偏好与工作区默认优先于浏览器语言,
   * 否则账号级语言偏好(i18n.md L1)将永不生效。
   */
  readonly systemLocales?: readonly string[] | null;
  /** 受支持清单;缺省 SUPPORTED_LOCALES */
  readonly supported?: readonly string[];
  /** 系统回退;缺省 'en' */
  readonly fallback?: string;
}

/**
 * 协商链:requested → userLocale → workspaceDefault → systemLocales → fallback。
 * 任一级缺省或非法即跳到下一级;绝不抛错(协商失败回退 fallback)。
 */
export function negotiateLocale(input: NegotiateLocaleInput): string {
  const supported = input.supported ?? SUPPORTED_LOCALES;
  const fallback = input.fallback ?? FALLBACK_LOCALE;
  const levels: ReadonlyArray<string | readonly string[] | null | undefined> = [
    toCandidates(input.requested),
    input.userLocale,
    input.workspaceDefaultLocale,
    input.systemLocales ?? null,
  ];
  for (const level of levels) {
    if (level === null || level === undefined || level.length === 0) continue;
    // 注意:string 不可直接交给 matchSupported(TS 允许 string 赋值给
    // readonly string[],运行时会按字符迭代)——此处显式归一为候选数组。
    const candidates: readonly string[] = typeof level === 'string' ? [level] : level;
    const matched = matchSupported(candidates, supported);
    if (matched !== null) return matched;
  }
  return fallback;
}

/**
 * 归一化为候选序列:
 * - 字符串视为 ?locale= 单值或 Accept-Language 头(交给 parseAcceptLanguage 统一解析);
 * - 数组视为已解析候选序列;
 * - null/undefined → null(协商链跳过本级)。
 */
function toCandidates(
  requested: string | readonly string[] | null | undefined,
): readonly string[] | null {
  if (requested === null || requested === undefined) return null;
  if (typeof requested === 'string') {
    return requested.length === 0 ? null : parseAcceptLanguage(requested);
  }
  return Array.isArray(requested) ? requested : null;
}
