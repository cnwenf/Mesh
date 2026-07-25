import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChannelCursors, CURSORS_STORAGE_KEY } from '../channelCursors';

function memoryStorage(): Pick<Storage, 'getItem' | 'setItem'> & { store: Map<string, string> } {
  const store = new Map<string, string>();
  return {
    store,
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
  };
}

describe('ChannelCursors', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns undefined for an unknown channel', () => {
    const cursors = new ChannelCursors(memoryStorage());
    expect(cursors.get('workspace:1:issues')).toBeUndefined();
  });

  it('stores and reads a per-channel seq', () => {
    const cursors = new ChannelCursors(memoryStorage());
    cursors.set('workspace:1:issues', 41);
    expect(cursors.get('workspace:1:issues')).toBe(41);
  });

  it('only advances — never stores a lower or equal seq', () => {
    const cursors = new ChannelCursors(memoryStorage());
    cursors.set('c', 10);
    cursors.set('c', 5);
    cursors.set('c', 10);
    expect(cursors.get('c')).toBe(10);
    cursors.set('c', 11);
    expect(cursors.get('c')).toBe(11);
  });

  it('clears a single channel without touching others', () => {
    const cursors = new ChannelCursors(memoryStorage());
    cursors.set('a', 1);
    cursors.set('b', 2);
    cursors.clear('a');
    expect(cursors.get('a')).toBeUndefined();
    expect(cursors.get('b')).toBe(2);
  });

  it('clear on a missing channel is a no-op that still persists', () => {
    const storage = memoryStorage();
    const cursors = new ChannelCursors(storage);
    cursors.set('a', 1);
    cursors.clear('does-not-exist');
    expect(cursors.get('a')).toBe(1);
  });

  it('setWatermark forces the cursor to the watermark even if lower', () => {
    const cursors = new ChannelCursors(memoryStorage());
    cursors.set('c', 100);
    cursors.setWatermark('c', 50);
    expect(cursors.get('c')).toBe(50);
  });

  it('all() returns a snapshot copy of every cursor', () => {
    const cursors = new ChannelCursors(memoryStorage());
    cursors.set('a', 1);
    cursors.set('b', 2);
    const snapshot = cursors.all();
    expect(snapshot).toEqual({ a: 1, b: 2 });
    snapshot.a = 999;
    expect(cursors.get('a')).toBe(1);
  });

  it('persists under mesh.rt.cursors.v1 as a JSON map', () => {
    const storage = memoryStorage();
    const cursors = new ChannelCursors(storage);
    cursors.set('view:7', 3);
    const raw = storage.store.get(CURSORS_STORAGE_KEY);
    expect(raw).toBeDefined();
    expect(JSON.parse(raw as string)).toEqual({ 'view:7': 3 });
  });

  it('hydrates from existing storage on construction', () => {
    const storage = memoryStorage();
    storage.store.set(CURSORS_STORAGE_KEY, JSON.stringify({ 'view:7': 12 }));
    const cursors = new ChannelCursors(storage);
    expect(cursors.get('view:7')).toBe(12);
  });

  it('defaults to window.localStorage when no storage is injected', () => {
    const cursors = new ChannelCursors();
    cursors.set('a', 5);
    expect(JSON.parse(localStorage.getItem(CURSORS_STORAGE_KEY) as string)).toEqual({ a: 5 });
    const reloaded = new ChannelCursors();
    expect(reloaded.get('a')).toBe(5);
  });

  it('ignores corrupt stored JSON instead of throwing', () => {
    const storage = memoryStorage();
    storage.store.set(CURSORS_STORAGE_KEY, '{not json');
    const cursors = new ChannelCursors(storage);
    expect(cursors.all()).toEqual({});
  });

  it('ignores non-object stored JSON and non-number values', () => {
    const storage = memoryStorage();
    storage.store.set(CURSORS_STORAGE_KEY, JSON.stringify([1, 2, 3]));
    expect(new ChannelCursors(storage).all()).toEqual({});
    storage.store.set(CURSORS_STORAGE_KEY, JSON.stringify({ a: 'x', b: 2 }));
    expect(new ChannelCursors(storage).all()).toEqual({ b: 2 });
  });

  it('survives a storage getItem failure (in-memory fallback)', () => {
    const failing: Pick<Storage, 'getItem' | 'setItem'> = {
      getItem: () => {
        throw new Error('boom');
      },
      setItem: () => {
        throw new Error('boom');
      },
    };
    const cursors = new ChannelCursors(failing);
    expect(() => cursors.set('a', 1)).not.toThrow();
    expect(cursors.get('a')).toBe(1);
  });

  it('does not throw when setItem fails after a successful load', () => {
    const storage = memoryStorage();
    storage.store.set(CURSORS_STORAGE_KEY, JSON.stringify({ a: 1 }));
    const setItem = vi.spyOn(storage, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    const cursors = new ChannelCursors(storage);
    expect(() => cursors.set('a', 2)).not.toThrow();
    expect(cursors.get('a')).toBe(2);
    setItem.mockRestore();
  });
});
