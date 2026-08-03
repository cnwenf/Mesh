import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// 异步断言默认超时 1s → 5s:coverage instrumentation 下渲染/请求链整体变慢,
// 1s 默认值在详情页多请求测试中产生时序抖动(MES-32 关联层编辑器挂载后尤甚)。
configure({ asyncUtilTimeout: 5000 });

// jsdom 不提供 matchMedia;ThemeProvider(system 模式)依赖它。
// 提供可编程 stub:默认 matches=false,测试可覆盖 window.matchMedia。
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

// jsdom 不提供 ResizeObserver;部分组件可能依赖。
if (!window.ResizeObserver) {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(window, 'ResizeObserver', {
    writable: true,
    value: ResizeObserverStub,
  });
}

// jsdom does not expose PointerEvent. Appica's Base UI controls dispatch a
// pointer click to their hidden native input so the browser form contract stays
// authoritative; a MouseEvent-compatible constructor is sufficient in tests.
if (!window.PointerEvent) {
  class PointerEventStub extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;

    constructor(type: string, params: PointerEventInit = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 0;
      this.pointerType = params.pointerType ?? 'mouse';
    }
  }
  Object.defineProperty(window, 'PointerEvent', {
    configurable: true,
    writable: true,
    value: PointerEventStub,
  });
}

afterEach(() => {
  cleanup();
  // 防御性:任何文件经 vi.stubGlobal 注入的全局(如 fetch 夹具)在此统一复位,
  // 杜绝跨文件污染(vitest 默认不自动 unstub)。各文件自身的 afterEach 仍保留。
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});
