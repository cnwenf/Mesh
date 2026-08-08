import { afterEach, describe, expect, it } from 'vitest';
import { useUnreadStore } from '../unreadStore';

describe('unreadStore(MES-189 L93 未读数全局镜像)', () => {
  afterEach(() => {
    useUnreadStore.setState({ count: 0 });
  });

  it('初始为 0', () => {
    expect(useUnreadStore.getState().count).toBe(0);
  });

  it('setCount 写入非负计数', () => {
    useUnreadStore.getState().setCount(7);
    expect(useUnreadStore.getState().count).toBe(7);
  });

  it('setCount 夹取负值为 0(乐观递减路径防御)', () => {
    useUnreadStore.getState().setCount(-3);
    expect(useUnreadStore.getState().count).toBe(0);
  });

  it('setCount 可重复覆盖(权威帧到达即对齐)', () => {
    useUnreadStore.getState().setCount(5);
    useUnreadStore.getState().setCount(2);
    expect(useUnreadStore.getState().count).toBe(2);
  });
});
