/**
 * 命令使用计数(面板空态「常用命令」排序依据,search-command-palette.md §4.2.1)。
 *
 * 纯前端 localStorage,键 `mesh.palette.cmdcount:{userId}`(按用户隔离);
 * 值为 commandId → 次数 的 JSON 映射。与 recents 同为本地信号,不参与服务端
 * 对象排序(§4.6 R2-H4:仅用于本地命令条目排序与空态组装)。
 * JSON 损坏/存储异常一律降级为空映射;写入返回不可变新映射。
 */

const COUNTS_KEY_PREFIX = 'mesh.palette.cmdcount';

/** 按用户隔离的计数键 */
export function commandCountsKey(userId: string): string {
  return `${COUNTS_KEY_PREFIX}:${userId}`;
}

function isCountsMap(value: unknown): value is Record<string, number> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  return Object.values(value as Record<string, unknown>).every(
    (item) => typeof item === 'number' && Number.isFinite(item) && item >= 0,
  );
}

/** 读取计数映射(键缺失/JSON 损坏 → 空映射) */
export function readCommandCounts(userId: string): Readonly<Record<string, number>> {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(commandCountsKey(userId));
  } catch {
    return {};
  }
  if (raw === null || raw === '') return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  return isCountsMap(parsed) ? { ...parsed } : {};
}

/** 命令执行一次 +1;持久化后返回新映射(不可变)。 */
export function incrementCommandCount(
  userId: string,
  commandId: string,
): Readonly<Record<string, number>> {
  const counts = { ...readCommandCounts(userId) };
  counts[commandId] = (counts[commandId] ?? 0) + 1;
  try {
    window.localStorage.setItem(commandCountsKey(userId), JSON.stringify(counts));
  } catch {
    // 存储不可用:降级为仅本次会话内存态,不阻断命令执行。
  }
  return counts;
}
