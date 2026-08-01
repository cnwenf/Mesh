/**
 * 单次生成流驱动 hook(chat-session.md §3.3 / README §6.8)。
 * 发送/重生成响应给出 stream_url 后由本 hook 启动 sse.streamChatGeneration,
 * 把 message.created/delta/done/interrupted/error 累积成一条「实时消息」(liveMessage)。
 * 终态后 isStreaming=false,liveMessage 保留供视图层提交入库,再由视图层调 reset() 清空;
 * 重连/续传(Last-Event-ID)内聚于 sse.ts。abort() 仅拆流,不调后端 stop(视图层负责)。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { parseStreamEvent, streamChatGeneration } from './sse';
import type { StreamHandle } from './sse';
import type { ChatMessage, ChatRole, GenerationStatus, SseFrame } from './types';

export interface UseChatStreamOptions {
  getToken: () => string | null;
  /** 测试注入:fetch / 定时器 / 随机源(透传 sse.ts)。 */
  fetchImpl?: typeof fetch;
  schedule?: (fn: () => void, ms: number) => void;
  random?: () => number;
}

export interface StartStreamParams {
  readonly streamUrl: string;
  readonly generationId: string;
  readonly sessionId: string;
}

export interface UseChatStream {
  /** 进行中的 agent 回复(终态后保留,待视图层提交);尚无 created 帧时为 null。 */
  readonly liveMessage: ChatMessage | null;
  readonly isStreaming: boolean;
  /** 流内 error 事件的 i18n 键(error.<code>);无错误为 null。 */
  readonly streamError: string | null;
  readonly activeGenerationId: string | null;
  readonly start: (params: StartStreamParams) => void;
  readonly abort: () => void;
  readonly reset: () => void;
}

/** 由 message.created 构造实时消息骨架(其余字段终态/视图层补)。 */
function buildStreamingMessage(
  sessionId: string,
  messageId: string,
  role: ChatRole,
  generationId: string,
): ChatMessage {
  const now = new Date().toISOString();
  return {
    id: messageId,
    session_id: sessionId,
    role,
    content: '',
    generation_id: generationId,
    generation_status: 'streaming',
    parent_id: null,
    selected_candidate: true,
    quote_message_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    error_message: null,
    started_at: now,
    finished_at: null,
    created_at: now,
    attachments: [],
    candidate_count: null,
    candidate_index: null,
  };
}

export function useChatStream(options: UseChatStreamOptions): UseChatStream {
  const [liveMessage, setLiveMessage] = useState<ChatMessage | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [activeGenerationId, setActiveGenerationId] = useState<string | null>(null);

  // 选项与句柄经 ref 持有:start 身份稳定,卸载/重启动可同步拆流。
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const handleRef = useRef<StreamHandle | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const generationIdRef = useRef<string | null>(null);

  const teardown = useCallback(() => {
    handleRef.current?.close();
    handleRef.current = null;
  }, []);

  const handleFrame = useCallback((frame: SseFrame) => {
    const event = parseStreamEvent(frame);
    if (event === null) return;
    const generationId = generationIdRef.current;
    switch (event.type) {
      case 'message.created':
        setLiveMessage(
          buildStreamingMessage(
            sessionIdRef.current ?? '',
            event.message_id,
            event.role,
            generationId ?? '',
          ),
        );
        return;
      case 'message.delta':
        setLiveMessage((prev) =>
          prev !== null && prev.id === event.message_id
            ? { ...prev, content: prev.content + event.delta }
            : prev,
        );
        return;
      case 'message.done':
        setLiveMessage((prev) =>
          prev !== null && prev.id === event.message_id
            ? {
                ...prev,
                generation_status: event.generation_status,
                completion_tokens: event.completion_tokens,
                finished_at: new Date().toISOString(),
              }
            : prev,
        );
        setIsStreaming(false);
        return;
      case 'message.interrupted':
        setLiveMessage((prev) =>
          prev !== null && prev.id === event.message_id
            ? {
                ...prev,
                content: event.partial_content !== '' ? event.partial_content : prev.content,
                generation_status: event.generation_status,
                finished_at: new Date().toISOString(),
              }
            : prev,
        );
        setIsStreaming(false);
        return;
      case 'error':
        setStreamError(`error.${event.code}`);
        setLiveMessage((prev) =>
          prev !== null && (event.message_id === null || prev.id === event.message_id)
            ? {
                ...prev,
                generation_status: 'failed' as GenerationStatus,
                error_message: event.message,
                finished_at: new Date().toISOString(),
              }
            : prev,
        );
        setIsStreaming(false);
        return;
      case 'ping':
        return;
    }
  }, []);

  const start = useCallback(
    (params: StartStreamParams) => {
      teardown();
      sessionIdRef.current = params.sessionId;
      generationIdRef.current = params.generationId;
      setActiveGenerationId(params.generationId);
      setStreamError(null);
      setLiveMessage(null);
      setIsStreaming(true);
      const opts = optionsRef.current;
      handleRef.current = streamChatGeneration({
        url: params.streamUrl,
        getToken: opts.getToken,
        onFrame: handleFrame,
        fetchImpl: opts.fetchImpl,
        schedule: opts.schedule,
        random: opts.random,
      });
    },
    [handleFrame, teardown],
  );

  const abort = useCallback(() => {
    teardown();
    // M4:本地即置 interrupted,避免终态 effect 把仍为 'streaming' 的 liveMessage
    // upsert 成永久流式幽灵(无 WS 二次对账时尤为明显)。服务端 stop 由视图层另发。
    setLiveMessage((prev) =>
      prev === null ? prev : { ...prev, generation_status: 'interrupted' as GenerationStatus },
    );
    setIsStreaming(false);
  }, [teardown]);

  const reset = useCallback(() => {
    setLiveMessage(null);
    setStreamError(null);
    setActiveGenerationId(null);
    setIsStreaming(false);
  }, []);

  // 卸载即拆流,杜绝卸载后 setState 与悬挂连接。
  useEffect(() => teardown, [teardown]);

  // §5.4 可见性恢复唤醒:页面重新可见时,若仍有活动流(handleRef 非空)则 nudge 一次,
  // 触发 sse 层单飞续传(切后台被冻结的连接得以恢复)。单飞守卫在 sse.nudge 内,
  // 多标签/快速切换不会风暴;终止后的句柄 nudge 为空操作(closed 守卫)。
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const onVisibilityChange = (): void => {
      if (document.visibilityState !== 'visible') return;
      handleRef.current?.nudge();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

  return { liveMessage, isStreaming, streamError, activeGenerationId, start, abort, reset };
}
