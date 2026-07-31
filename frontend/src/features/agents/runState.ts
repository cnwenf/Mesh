/**
 * Agent 运行态归一(design-quality.md §9.8 统一语言):presence 容量三元组 → 运行态 RunState。
 *
 * 纯函数、单一事实源:名册页的 agent 行与 agent 详情页头部共用同一映射,
 * 杜绝各处各自着色/各自命名(§9.8「同一执行使用相同文案、图标和 tone」)。
 * 图标与 tone 由 design 层 RunStateBadge 集中持有,本模块只负责「三元组 → 状态」。
 */
import type { RunState } from '../../design';

/** presence 容量三元组(运行中 / 排队 / 等待人工确认)。 */
export interface PresenceTriple {
  readonly running: number;
  readonly queued: number;
  readonly awaiting: number;
}

/**
 * 三元组 → 运行态(§9.8 五态 + idle/unknown 派生态):
 * - 无帧(null,帧未至)→ `unknown`;
 * - running > 0 → `running`(优先级最高:正在跑即「运行中」);
 * - queued > 0 → `queued`;
 * - awaiting > 0 → `waiting`(等待人工确认);
 * - 三者全 0 → `idle`(无在途执行)。
 */
export function presenceToRunState(presence: PresenceTriple | null): RunState {
  if (presence === null) return 'unknown';
  if (presence.running > 0) return 'running';
  if (presence.queued > 0) return 'queued';
  if (presence.awaiting > 0) return 'waiting';
  return 'idle';
}
