/**
 * Agent presence 订阅 hook(README §6.7/§6.12,design-quality.md §9.8)。
 *
 * 名册页人机同册,多个 agent 行各自一条 `agent:{id}:presence` 频道;本 hook 一次性
 * 订阅全部 agent id 的 presence 频道,把帧聚合为 `agentId → 容量三元组` 只读映射,
 * 供名册页逐行经 `presenceToRunState` 渲染运行态徽标。
 *
 * 设计要点:
 * - 帧过滤以「频道是否属于已订阅集合」为准(而非事件名),与订阅清单一一对应;
 * - id 列表变化(以 join 后的字符串作 effect 依赖)即整体退订重订,避免泄漏;
 * - REST 快照先填充映射,realtime 绝对帧到达后按 agent id 整体覆盖;
 * - realtime 为 null(shell 外)时仍保留调用方提供的 REST 快照;
 * - `parsePresenceFrame` 为纯函数,单测独立覆盖各分支。
 */
import { useEffect, useState } from 'react';
import { useRealtimeContext } from '../../shell/AppShell';
import type { RealtimeEventFrame } from '../../types/realtime';
import { agentPresenceChannel } from './api';
import type { PresenceTriple } from './runState';

/** presence 频道形状:`agent:{id}:presence`(与 api.agentPresenceChannel 同源)。 */
const PRESENCE_CHANNEL_PATTERN = /^agent:(.+):presence$/;

/** join 分隔符:agent id 为 UUID,不含空白,join/split 往返不歧义。 */
const IDS_KEY_SEPARATOR = '\u0000';
const EMPTY_INITIAL_PRESENCE: ReadonlyMap<string, PresenceTriple> = new Map();

interface PresenceFramePayload {
  readonly running?: number;
  readonly queued?: number;
  readonly awaiting_approval?: number;
}

export interface ParsedPresenceFrame {
  readonly id: string;
  readonly triple: PresenceTriple;
}

type PresenceEntry = readonly [string, PresenceTriple];

function initialPresenceKey(
  agentIds: readonly string[],
  initialPresence: ReadonlyMap<string, PresenceTriple>,
): string {
  const entries: PresenceEntry[] = [];
  for (const id of agentIds) {
    const triple = initialPresence.get(id);
    if (triple !== undefined) entries.push([id, triple]);
  }
  return JSON.stringify(entries);
}

function presenceMapFromKey(key: string): ReadonlyMap<string, PresenceTriple> {
  return new Map(JSON.parse(key) as PresenceEntry[]);
}

/**
 * 解析 presence 帧:非 `agent:{id}:presence` 频道 → null;payload 缺字段回退 0。
 * 纯函数,不依赖任何订阅态(频道匹配在 hook 内按订阅集合二次收敛)。
 */
export function parsePresenceFrame(frame: RealtimeEventFrame): ParsedPresenceFrame | null {
  const match = PRESENCE_CHANNEL_PATTERN.exec(frame.channel);
  if (match === null) return null;
  const payload = frame.payload as PresenceFramePayload;
  return {
    id: match[1],
    triple: {
      running: payload.running ?? 0,
      queued: payload.queued ?? 0,
      awaiting: payload.awaiting_approval ?? 0,
    },
  };
}

/**
 * 订阅若干 agent 的 presence 频道,返回 `agentId → 容量三元组` 只读映射。
 * 有 REST 快照的 agent 首屏即在映射内；无快照且帧未至时仍为 unknown。
 */
export function useAgentPresenceMap(
  agentIds: readonly string[],
  initialPresence: ReadonlyMap<string, PresenceTriple> = EMPTY_INITIAL_PRESENCE,
): ReadonlyMap<string, PresenceTriple> {
  const realtime = useRealtimeContext();
  const idsKey = agentIds.join(IDS_KEY_SEPARATOR);
  const initialKey = initialPresenceKey(agentIds, initialPresence);
  const [presenceMap, setPresenceMap] = useState<ReadonlyMap<string, PresenceTriple>>(() =>
    presenceMapFromKey(initialKey),
  );
  // 以 join 后的字符串作依赖:id 列表内容变化才重订,数组引用变化(内容不变)不重订。

  useEffect(() => {
    // M1:名单变化(切 tab/搜索/移除成员)即清空映射,避免已离列 agent 的旧三元组
    // 永久滞留、再入列时在新帧到达前短暂渲染陈旧运行态。
    setPresenceMap(presenceMapFromKey(initialKey));
    // shell 外(登录页/独立渲染)无 realtime → 保留 REST 初始映射。
    if (realtime === null) {
      return;
    }
    const ids = idsKey === '' ? [] : idsKey.split(IDS_KEY_SEPARATOR);
    if (ids.length === 0) {
      return;
    }
    const channels = new Set(ids.map((id) => agentPresenceChannel(id)));
    for (const channel of channels) {
      realtime.client.subscribe(channel);
    }
    const unsubscribe = realtime.client.onFrame((frame) => {
      // 仅收敛已订阅频道的帧,异频道(其它 agent / 其它域)忽略。
      if (!channels.has(frame.channel)) return;
      const parsed = parsePresenceFrame(frame);
      if (parsed === null) return;
      setPresenceMap((prev) => {
        const next = new Map(prev);
        next.set(parsed.id, parsed.triple);
        return next;
      });
    });
    return () => {
      unsubscribe();
      for (const channel of channels) {
        realtime.client.unsubscribe(channel);
      }
    };
  }, [realtime, idsKey, initialKey]);

  return presenceMap;
}
