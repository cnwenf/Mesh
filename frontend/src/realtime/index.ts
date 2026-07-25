/**
 * 实时层桶导出(README §6.7 实时客户端,T2)。
 */
export { ChannelCursors, CURSORS_STORAGE_KEY } from './channelCursors';
export { RealtimeClient } from './RealtimeClient';
export type { ConnectionState, RealtimeClientOptions, ResyncRequest } from './RealtimeClient';
export { mergeEntityFrame } from './merge';
export type { MergeContext } from './merge';
export { PollingFallback } from './pollingFallback';
export type { PollingFallbackOptions, PollingSource, PollingState } from './pollingFallback';
export { useRealtime } from './useRealtime';
export type { UseRealtimeOptions, UseRealtimeResult } from './useRealtime';
