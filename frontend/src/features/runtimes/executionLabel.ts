/**
 * 执行展示标签工具 —— 契约保真约束。
 *
 * 后端 `_render_execution`（runtime.md §3.3）不返回 agent_name / issue_identifier，
 * 只返回 id / agent_id / issue_id / trigger / queued_at 等入队快照字段。标签因此
 * 一律由「契约实际提供的字段」组成：trigger 文案 + 执行短 ID（视图层再辅以相对
 * 时间），杜绝依赖后端不提供的字段而在真实环境退化为无信息常量。
 */
import type { TranslateFn } from '../../i18n';

/** 执行短 ID 长度上限：UUID 首段恰为 8 个十六进制字符。 */
export const EXECUTION_SHORT_ID_LENGTH = 8;

/** 执行 ID → 短 ID（取首段；短于上限的 ID 原样返回，如 e2e 契约栈的语义化 ID）。 */
export function executionShortId(id: string): string {
  return id.slice(0, EXECUTION_SHORT_ID_LENGTH);
}

/** 后端允许的 trigger 全集（§2.2 task_executions.trigger 检查约束）。 */
const KNOWN_TRIGGERS: ReadonlySet<string> = new Set([
  'assign',
  'mention',
  'autopilot',
  'manual',
  'chat',
  'integration',
]);

/**
 * trigger → i18n 键（复用详情页文案 runtimes.execution.triggerKind.*）。
 * 未知值落通用键而非拼出不存在的键，避免触发缺失上报。
 */
export function executionTriggerLabelKey(trigger: string): string {
  return KNOWN_TRIGGERS.has(trigger)
    ? 'runtimes.execution.triggerKind.' + trigger
    : 'runtimes.execution.triggerKind.unknown';
}

/** 标签所需最小字段集（ExecutionSummary 子集，便于各页面复用）。 */
export interface ExecutionLabelInput {
  readonly id: string;
  readonly trigger: string;
}

/** 行标签规范形：trigger 文案 · 短 ID（如「分派 · 5f1c2a6e」/「Assign · exec-1」）。 */
export function executionDisplayLabel(t: TranslateFn, execution: ExecutionLabelInput): string {
  return t(executionTriggerLabelKey(execution.trigger)) + ' · ' + executionShortId(execution.id);
}
