/**
 * 会话 agent 头像(chat-session.md §4.1)。avatar_url 存在则渲染 <img>,
 * 加载失败(onError:链接失效/对象更替)回退到名称首字母占位;avatar_url 为 null
 * 直接渲染首字母占位。占位为 aria-hidden(读屏由相邻的 agent 名称承载)。
 */
import { useEffect, useState } from 'react';
import type { ChatAgentRef } from './types';

export interface AgentAvatarProps {
  readonly agent: ChatAgentRef;
  /** 头像测试标识(如 `chat-session-avatar-{id}`)。 */
  readonly testId: string;
}

/** 名称首字母(大写);空名称回退 '?'。 */
function initialOf(name: string): string {
  const trimmed = name.trim();
  return trimmed === '' ? '?' : trimmed.charAt(0).toUpperCase();
}

export function AgentAvatar(props: AgentAvatarProps): React.JSX.Element {
  const { agent } = props;
  const [broken, setBroken] = useState(false);

  // avatar_url 变更(切换到另一 agent)时重置失败标记,使新头像可再次尝试加载。
  useEffect(() => {
    setBroken(false);
  }, [agent.avatar_url]);

  const showImage = agent.avatar_url !== null && !broken;
  if (showImage) {
    return (
      <img
        className="mesh-chat__avatar"
        data-testid={props.testId}
        src={agent.avatar_url as string}
        alt={agent.name}
        loading="lazy"
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <span
      className="mesh-chat__avatar mesh-chat__avatar--fallback"
      data-testid={props.testId}
      aria-hidden="true"
    >
      {initialOf(agent.name)}
    </span>
  );
}
