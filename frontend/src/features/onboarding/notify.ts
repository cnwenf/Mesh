/**
 * 模块内变更通知(onboarding.md §4.2 / §1.2.2 O9)。
 *
 * 两类广播,均为「该做什么」的信号,数据库仍是唯一真源:
 * 1. 外部变更:帮助菜单 / 命令面板的「恢复上手清单」在 shell 之外(App 根部)
 *    发起,不经 useOnboarding 写路径,也不产生实时帧(dismiss/restore 不发
 *    realtime 事件)。恢复成功后广播一次,useOnboarding 订阅即重拉。
 * 2. 乐观推进请求:空状态主操作完成(建 agent / 发邀请 / 建 issue)后,页面
 *    请求 useOnboarding 乐观置位对应步骤 + POST 手动完成 + 失败回滚,服务端
 *    领域事件复核收敛(§1.2.2 末注「乐观 UI + 服务端领域事件复核」)。
 */
import type { OnboardingStepKey } from './types';

type Listener = () => void;

const listeners = new Set<Listener>();

/** 订阅外部变更;返回取消订阅函数。 */
export function onOnboardingExternalChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 广播一次外部变更(恢复成功等)。 */
export function notifyOnboardingExternalChange(): void {
  for (const listener of [...listeners]) listener();
}

export interface StepOptimisticRequest {
  readonly stepKey: OnboardingStepKey;
}

type StepListener = (request: StepOptimisticRequest) => void;

const stepListeners = new Set<StepListener>();

/** 订阅乐观推进请求;返回取消订阅函数。 */
export function onStepOptimisticRequest(listener: StepListener): () => void {
  stepListeners.add(listener);
  return () => {
    stepListeners.delete(listener);
  };
}

/** 空状态主操作完成后请求乐观推进对应清单步骤(§1.2.2 O9)。 */
export function requestOptimisticStepComplete(stepKey: OnboardingStepKey): void {
  for (const listener of [...stepListeners]) listener({ stepKey });
}
