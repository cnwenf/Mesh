/**
 * 虚拟滚动窗口计算测试(design-quality §11.4 / kanban §5.3):
 * start/end/offset/total、overscan、钳制、零项、滚动偏移、hook memo。
 */
import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  CARD_HEIGHT,
  VIRTUALIZE_THRESHOLD,
  computeVirtualWindow,
  useVirtualWindow,
} from '../useVirtualWindow';

describe('computeVirtualWindow 窗口数学', () => {
  it('基本窗口:可见 + overscan,totalHeight = count*itemHeight', () => {
    const result = computeVirtualWindow({
      itemCount: 300,
      itemHeight: 84,
      viewportHeight: 600,
      scrollTop: 0,
      overscan: 3,
    });
    // 可见 ceil(600/84)=8;start=max(0,0-3)=0;end=min(300,8+3)=11。
    expect(result.start).toBe(0);
    expect(result.end).toBe(11);
    expect(result.offsetY).toBe(0);
    expect(result.totalHeight).toBe(300 * 84);
  });

  it('滚动后窗口平移,offsetY = start*itemHeight', () => {
    const result = computeVirtualWindow({
      itemCount: 300,
      itemHeight: 84,
      viewportHeight: 600,
      scrollTop: 8400, // 第 100 项起
      overscan: 3,
    });
    expect(result.start).toBe(97); // 100-3
    expect(result.end).toBe(111); // 100+8+3
    expect(result.offsetY).toBe(97 * 84);
  });

  it('overscan=0:仅可见范围', () => {
    const result = computeVirtualWindow({
      itemCount: 100,
      itemHeight: 50,
      viewportHeight: 200,
      scrollTop: 0,
      overscan: 0,
    });
    expect(result.start).toBe(0);
    expect(result.end).toBe(4); // ceil(200/50)=4
  });

  it('末尾钳制:end 不超过 itemCount', () => {
    const result = computeVirtualWindow({
      itemCount: 10,
      itemHeight: 84,
      viewportHeight: 600,
      scrollTop: 0,
      overscan: 5,
    });
    expect(result.end).toBe(10);
  });

  it('scrollTop 越界向下钳制到最大可滚动', () => {
    const result = computeVirtualWindow({
      itemCount: 20,
      itemHeight: 84,
      viewportHeight: 600,
      scrollTop: 999999,
      overscan: 3,
    });
    // 最大滚动 = 20*84-600=1080 → rawStart=floor(1080/84)=12。
    expect(result.start).toBe(9); // 12-3
    expect(result.end).toBe(20);
  });

  it('零项 / 零高度 → 空窗口', () => {
    expect(computeVirtualWindow({ itemCount: 0, itemHeight: 84, viewportHeight: 600, scrollTop: 0 })).toEqual({
      start: 0,
      end: 0,
      offsetY: 0,
      totalHeight: 0,
    });
    expect(computeVirtualWindow({ itemCount: 10, itemHeight: 0, viewportHeight: 600, scrollTop: 0 })).toEqual({
      start: 0,
      end: 0,
      offsetY: 0,
      totalHeight: 0,
    });
  });

  it('负 scrollTop 钳制为 0', () => {
    const result = computeVirtualWindow({
      itemCount: 100,
      itemHeight: 84,
      viewportHeight: 600,
      scrollTop: -500,
      overscan: 3,
    });
    expect(result.start).toBe(0);
    expect(result.offsetY).toBe(0);
  });
});

describe('useVirtualWindow hook', () => {
  it('memo 化:相同输入返回相等结果', () => {
    const { result, rerender } = renderHook(
      (props: { scrollTop: number }) =>
        useVirtualWindow({ itemCount: 300, itemHeight: 84, viewportHeight: 600, scrollTop: props.scrollTop }),
      { initialProps: { scrollTop: 0 } },
    );
    const first = result.current;
    rerender({ scrollTop: 0 });
    expect(result.current).toBe(first);
    rerender({ scrollTop: 840 });
    expect(result.current).not.toBe(first);
  });

  it('常量符合 spec:阈值 200,卡高 84', () => {
    expect(VIRTUALIZE_THRESHOLD).toBe(200);
    expect(CARD_HEIGHT).toBe(84);
  });
});
