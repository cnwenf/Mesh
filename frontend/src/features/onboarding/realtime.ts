/**
 * 上手引导实时帧识别(onboarding.md §3.7,README §6.7)。
 * 纯函数:绝不触网、绝不改入参。频道 member:{member_id}:onboarding 上的事件:
 * - onboarding.progress(任一步骤完成/跳过)载荷 {state_id, checklist, step_key, status, completed_via, progress};
 * - onboarding.completed(aha 首次置位)载荷 {state_id, checklist, aha_reached_at, progress}。
 * 进度真源在数据库,任何本频道帧均触发整拉 GET state(最简正确合并,§3.7 降级等价)。
 */
import type { RealtimeEventFrame } from '../../types/realtime';
import { isOnboardingStepKey } from './types';
import type { OnboardingStepKey } from './types';

export type OnboardingEventKind = 'progress' | 'completed';

export interface OnboardingFrameInfo {
  readonly stateId: string | null;
  readonly kind: OnboardingEventKind;
  /** 仅 progress 帧携带(completed 帧为 null) */
  readonly stepKey: OnboardingStepKey | null;
}

const PROGRESS_EVENT = 'onboarding.progress';
const COMPLETED_EVENT = 'onboarding.completed';

/** 帧是否属于本模块:op=event、频道匹配且事件名命中词汇注册表(§6.7 不另立事件名)。 */
export function isOnboardingFrame(frame: RealtimeEventFrame, channelId: string): boolean {
  if (frame.op !== 'event') return false;
  if (frame.channel !== channelId) return false;
  return frame.event === PROGRESS_EVENT || frame.event === COMPLETED_EVENT;
}

/**
 * 解析本模块事件帧:非本模块帧返回 null;progress/completed 取出 state_id 与 step_key。
 * 载荷字段缺失时对应项为 null(不信任远端载荷,边界处校验,§6.15)。
 */
export function parseOnboardingFrame(frame: RealtimeEventFrame): OnboardingFrameInfo | null {
  if (frame.op !== 'event') return null;
  if (frame.event !== PROGRESS_EVENT && frame.event !== COMPLETED_EVENT) return null;
  const payload = frame.payload as Record<string, unknown>;
  const stateId = typeof payload.state_id === 'string' ? payload.state_id : null;
  if (frame.event === COMPLETED_EVENT) {
    return { stateId, kind: 'completed', stepKey: null };
  }
  const rawStepKey = payload.step_key;
  const stepKey =
    typeof rawStepKey === 'string' && isOnboardingStepKey(rawStepKey) ? rawStepKey : null;
  return { stateId, kind: 'progress', stepKey };
}
