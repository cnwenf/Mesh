/**
 * 候选回复分支切换器(chat-session.md §4.2 / §3.2)。
 * ‹ i/n › 仅切换「本地查看索引」:首次翻页按 parent_id 懒加载该父的全部候选,
 * 其后翻页只在已加载候选间移动查看位置,**不写库**(无 select 调用);
 * 「使用此条」(chat-candidate-use-*)为独立的显式落库入口,经 selectCandidate
 * (POST messages/{id}/select)持久化选中并回调父级。已持久化的候选以「✓ 已选用」
 * 标记,与正在预览(查看)的候选区分。索引越界做钳制(不环绕),两端禁用对应按钮。
 */
import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import type { MeshApiClient } from '../../api';
import { Button, useToast } from '../../design';
import { useT } from '../../i18n';
import { listChatMessages, selectCandidate } from './api';
import { toErrorKey } from './errors';
import type { ChatMessage } from './types';

export interface CandidateSwitcherProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly sessionId: string;
  /** 候选共同父消息 id(列表查询 parent_id)。 */
  readonly parentId: string;
  /** 当前已持久化选中候选消息 id(select 端点路径参数 / 选中标记依据)。 */
  readonly selectedId: string;
  /** 已持久化选中候选的索引(0 基,来自 candidate_index)。 */
  readonly index: number;
  readonly count: number;
  /** 已持久化选中候选消息对象(候选列表加载前的兜底渲染)。 */
  readonly selectedMessage: ChatMessage;
  /** 渲染当前查看候选的气泡(父级提供 MessageBubble,解耦组件)。 */
  readonly renderCandidate: (message: ChatMessage) => ReactNode;
  /** 「使用此条」落库成功后回调:父级以新选中消息替换展示。 */
  readonly onSelected: (message: ChatMessage) => void;
}

function clampIndex(index: number, total: number): number {
  return Math.max(0, Math.min(index, total - 1));
}

export function CandidateSwitcher(props: CandidateSwitcherProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [candidates, setCandidates] = useState<readonly ChatMessage[] | null>(null);
  const [viewingIndex, setViewingIndex] = useState(() => clampIndex(props.index, props.count));
  const [busy, setBusy] = useState(false);

  const ensureCandidates = useCallback(async (): Promise<readonly ChatMessage[]> => {
    if (candidates !== null) return candidates;
    const page = await listChatMessages(props.client, props.workspaceId, props.sessionId, {
      parent_id: props.parentId,
      limit: 50,
    });
    setCandidates(page.data);
    return page.data;
  }, [candidates, props.client, props.workspaceId, props.sessionId, props.parentId]);

  // 本地翻页:仅移动查看索引(加载候选后钳制),绝不写库。
  const goTo = useCallback(
    async (target: number) => {
      try {
        const list = await ensureCandidates();
        setViewingIndex(clampIndex(target, list.length));
      } catch (err) {
        toast.addToast(t(toErrorKey(err)), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [ensureCandidates, toast, t],
  );

  // 「使用此条」:把当前查看的候选持久化为选中(select 落库)并回调父级。
  const handleUse = useCallback(async () => {
    setBusy(true);
    try {
      const list = await ensureCandidates();
      const target = list[viewingIndex];
      if (target === undefined) return;
      await selectCandidate(
        props.client,
        props.workspaceId,
        props.sessionId,
        props.parentId,
        target.id,
      );
      props.onSelected(target);
    } catch (err) {
      toast.addToast(t(toErrorKey(err)), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [ensureCandidates, viewingIndex, props, toast, t]);

  const total = candidates !== null ? candidates.length : props.count;
  const displayIndex = clampIndex(viewingIndex, total);
  const atStart = displayIndex <= 0;
  const atEnd = displayIndex >= total - 1;
  // 当前查看的候选:候选已加载取对应项,否则兜底已持久化选中消息(displayIndex 已钳制合法)。
  const viewed: ChatMessage =
    candidates !== null ? (candidates[displayIndex] as ChatMessage) : props.selectedMessage;
  // 当前查看的候选是否即已持久化选中项(用于「✓ 已选用」标记与禁用「使用此条」)。
  const isViewingSelected = viewed.id === props.selectedId;

  return (
    <div className="mesh-chat__candidate-group" data-testid={`chat-candidates-${props.selectedId}`}>
      {props.renderCandidate(viewed)}
      <div className="mesh-chat__candidates">
        <Button
          variant="ghost"
          size="sm"
          aria-label={t('chat.candidate.prev')}
          data-testid={`chat-candidate-prev-${props.selectedId}`}
          disabled={atStart}
          onClick={() => void goTo(displayIndex - 1)}
        >
          ‹
        </Button>
        <span className="mesh-chat__candidates-indicator" data-testid="chat-candidate-indicator">
          {t('chat.candidate.position', { index: displayIndex + 1, total })}
        </span>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t('chat.candidate.next')}
          data-testid={`chat-candidate-next-${props.selectedId}`}
          disabled={atEnd}
          onClick={() => void goTo(displayIndex + 1)}
        >
          ›
        </Button>
        {isViewingSelected ? (
          <span
            className="mesh-chat__candidate-selected"
            data-testid="chat-candidate-selected-mark"
          >
            ✓ {t('chat.candidate.selected')}
          </span>
        ) : null}
        <Button
          variant="secondary"
          size="sm"
          className="mesh-chat__candidate-use"
          data-testid={`chat-candidate-use-${props.selectedId}`}
          disabled={busy || isViewingSelected}
          isLoading={busy}
          onClick={() => void handleUse()}
        >
          {t('chat.candidate.use')}
        </Button>
      </div>
    </div>
  );
}
