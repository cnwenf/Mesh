/**
 * 模块内变更通知(onboarding.md §4.2 流程 3)。
 *
 * 帮助菜单 / 命令面板的「恢复上手清单」在 shell 之外(App 根部)发起,
 * 不经过 useOnboarding 的写路径,也不产生实时帧(dismiss/restore 不发
 * realtime 事件)。恢复成功后广播一次,useOnboarding 订阅即重拉,清单
 * 按库内进度即时重现——DB 仍是唯一真源,这只是一次「该重拉了」的信号。
 */
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
