import { describe, expect, it } from 'vitest';
import * as realtime from '../index';

describe('realtime barrel exports', () => {
  it('exposes the full public API', () => {
    expect(realtime.ChannelCursors).toBeTypeOf('function');
    expect(realtime.CURSORS_STORAGE_KEY).toBe('mesh.rt.cursors.v1');
    expect(realtime.RealtimeClient).toBeTypeOf('function');
    expect(realtime.mergeEntityFrame).toBeTypeOf('function');
    expect(realtime.PollingFallback).toBeTypeOf('function');
    expect(realtime.useRealtime).toBeTypeOf('function');
  });
});
