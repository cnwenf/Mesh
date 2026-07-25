/**
 * 列表/视图 filters 客户端预校验与错误归类 — 权威:docs/specs/README.md §6.14。
 * 限制:最大嵌套深度 3、最大条件数 20;服务端超限返回
 * `400 filter_too_complex` / `422 query_cost_exceeded`。
 */
import { MeshApiError } from './errors';

export type FilterCondition = { field: string; op: string; value: unknown };
export type FilterGroup = { and: FilterNode[] } | { or: FilterNode[] };
export type FilterNode = FilterCondition | FilterGroup;

export const MAX_FILTER_DEPTH = 3;
export const MAX_FILTER_CONDITIONS = 20;

function isGroup(node: FilterNode): node is FilterGroup {
  return 'and' in node || 'or' in node;
}

function groupChildren(node: FilterGroup): readonly FilterNode[] {
  return 'and' in node ? node.and : node.or;
}

export interface FilterMeasurement {
  depth: number;
  conditionCount: number;
}

/**
 * 深度计数:裸条件 = 1;`{and:[cond,cond]}` = 2;嵌套分组再 +1。
 * conditionCount 为子树内条件总数。
 */
export function measureFilters(node: FilterNode): FilterMeasurement {
  if (!isGroup(node)) {
    return { depth: 1, conditionCount: 1 };
  }
  let maxChildDepth = 0;
  let conditionCount = 0;
  for (const child of groupChildren(node)) {
    const measured = measureFilters(child);
    maxChildDepth = Math.max(maxChildDepth, measured.depth);
    conditionCount += measured.conditionCount;
  }
  return { depth: 1 + maxChildDepth, conditionCount };
}

/** 超限时抛 MeshApiError(400, filter_too_complex,details 携带越界量与上限)。 */
export function validateFilters(node: FilterNode): void {
  const { depth, conditionCount } = measureFilters(node);
  if (depth > MAX_FILTER_DEPTH) {
    throw new MeshApiError({
      status: 400,
      code: 'filter_too_complex',
      message: 'filter too complex',
      details: { depth, max: MAX_FILTER_DEPTH },
    });
  }
  if (conditionCount > MAX_FILTER_CONDITIONS) {
    throw new MeshApiError({
      status: 400,
      code: 'filter_too_complex',
      message: 'filter too complex',
      details: { conditionCount, max: MAX_FILTER_CONDITIONS },
    });
  }
}

/** 归类服务端过滤错误:400+filter_too_complex / 422+query_cost_exceeded;其余 null。 */
export function classifyFilterError(
  err: MeshApiError,
): 'filter_too_complex' | 'query_cost_exceeded' | null {
  if (err.status === 400 && err.code === 'filter_too_complex') {
    return 'filter_too_complex';
  }
  if (err.status === 422 && err.code === 'query_cost_exceeded') {
    return 'query_cost_exceeded';
  }
  return null;
}
