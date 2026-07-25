/**
 * 消息目录加载 / 版本 / 缺 key 三级回退 — i18n.md §2.4/§2.5/§3.1/§3.2。
 *
 * - `version` 为目录内容的不可变哈希(djb2,确定性,不依赖 Date/random),内容变更即换版本;
 * - 远端拉取走 ETag/If-None-Match 语义:客户端携带 `If-None-Match: <cached.version>`,
 *   304 → 沿用本地缓存,200 → 替换;
 * - 内置 en + zh-CN 目录静态打包,离线可用(默认路径不依赖网络);
 * - 缺 key 三级回退:请求 locale → en → key 本身,单点回退不中断整页(§2.5)。
 */
import enCatalogJson from './catalogs/en.json';
import zhCNCatalogJson from './catalogs/zh-CN.json';

export interface Catalog {
  readonly locale: string;
  readonly version: string;
  readonly messages: Record<string, string>;
}

const DJB2_SEED = 5381;
const DJB2_FACTOR = 33;
const HEX_HASH_LENGTH = 8;
const ENTRY_SEPARATOR = '\u0000';

/**
 * 计算目录内容的稳定版本哈希(8 位小写十六进制,djb2)。
 * 按键排序后序列化,与插入顺序无关;确定性输出,不含 Date/random。
 */
export function computeCatalogVersion(messages: Record<string, string>): string {
  let hash = DJB2_SEED;
  for (const key of Object.keys(messages).sort()) {
    const entry = `${key}=${messages[key]}${ENTRY_SEPARATOR}`;
    for (let index = 0; index < entry.length; index += 1) {
      hash = (Math.imul(hash, DJB2_FACTOR) + entry.charCodeAt(index)) >>> 0;
    }
  }
  return hash.toString(16).padStart(HEX_HASH_LENGTH, '0');
}

export type MessageFallbackLevel = 'none' | 'en' | 'key';

export interface ResolvedMessage {
  readonly text: string;
  readonly fallback: MessageFallbackLevel;
}

/**
 * 缺 key 三级回退(§2.5):主 locale → en → key 本身。
 * 回退仅影响该 key 的单点渲染,不中断整页;命中回退由调用方上报(§4.5)。
 */
export function resolveMessage(
  catalogs: { readonly primary?: Catalog; readonly fallback?: Catalog },
  key: string,
): ResolvedMessage {
  const primaryText = catalogs.primary?.messages[key];
  if (typeof primaryText === 'string') return { text: primaryText, fallback: 'none' };
  const fallbackText = catalogs.fallback?.messages[key];
  if (typeof fallbackText === 'string') return { text: fallbackText, fallback: 'en' };
  return { text: key, fallback: 'key' };
}

export interface CatalogFetchResponse {
  readonly status: number;
  readonly body?: Catalog;
}

export type CatalogFetcher = (
  url: string,
  init?: { headers?: Record<string, string> },
) => Promise<CatalogFetchResponse>;

export const CATALOG_ENDPOINT = '/api/v1/i18n/catalog';

const HTTP_OK = 200;
const HTTP_NOT_MODIFIED = 304;

export interface LoadCatalogOptions {
  /** 可注入的传输层(测试与 e2e 注入 mock;生产注入鉴权 fetch) */
  readonly fetcher: CatalogFetcher;
  /** API 基础路径,缺省为同源相对路径 */
  readonly baseUrl?: string;
  /** 本地缓存目录;提供时携带 If-None-Match,304 沿用缓存 */
  readonly cached?: Catalog | null;
}

/**
 * 按 ETag 版本语义拉取目录(§3.1/§3.2):
 * - 有缓存 → 请求携带 `If-None-Match: <cached.version>`;
 * - 304 → 沿用本地缓存;200 → 校验并返回新目录;
 * - 其余状态 / 结构非法 → 抛错(由调用方归类)。
 */
export async function loadCatalog(locale: string, opts: LoadCatalogOptions): Promise<Catalog> {
  if (typeof locale !== 'string' || locale.trim().length === 0) {
    throw new Error('loadCatalog: locale must be a non-empty BCP-47 tag');
  }
  const url = `${opts.baseUrl ?? ''}${CATALOG_ENDPOINT}?locale=${encodeURIComponent(locale)}`;
  const init = opts.cached ? { headers: { 'If-None-Match': opts.cached.version } } : undefined;
  const response = await opts.fetcher(url, init);
  if (response.status === HTTP_NOT_MODIFIED) {
    if (!opts.cached) {
      throw new Error(`loadCatalog: received 304 without a local cache for locale "${locale}"`);
    }
    return opts.cached;
  }
  if (response.status !== HTTP_OK) {
    throw new Error(`Failed to load catalog for locale "${locale}": HTTP ${response.status}`);
  }
  if (!isCatalog(response.body)) {
    throw new Error(`Invalid catalog payload for locale "${locale}"`);
  }
  return response.body;
}

/** 目录结构守卫:不信任远端载荷,边界处校验(§6.15 不可信内容处理)。 */
function isCatalog(value: unknown): value is Catalog {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.locale !== 'string' || typeof candidate.version !== 'string') return false;
  const messages = candidate.messages;
  if (typeof messages !== 'object' || messages === null || Array.isArray(messages)) return false;
  return Object.values(messages as Record<string, unknown>).every(
    (entry) => typeof entry === 'string',
  );
}

/** 静态内置目录:离线可用默认,en 为权威源语言(§2.5)。 */
export const builtinCatalogs: Record<string, Catalog> = Object.freeze({
  [enCatalogJson.locale]: enCatalogJson,
  [zhCNCatalogJson.locale]: zhCNCatalogJson,
});
