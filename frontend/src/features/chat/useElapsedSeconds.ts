/**
 * 流式已耗时秒数(§9.8 运行反馈):active 期间每秒 +1,失活即归零。
 * 独立小钩子供 ConversationPanel 头部「运行中 · Ns」呈现。
 */
import { useEffect, useState } from 'react';

export function useElapsedSeconds(isActive: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!isActive) {
      setSeconds(0);
      return;
    }
    const id = setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => clearInterval(id);
  }, [isActive]);
  return seconds;
}
