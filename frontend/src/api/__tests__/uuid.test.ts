import { afterEach, describe, expect, it, vi } from 'vitest';
import { uuidv4 } from '../uuid';

/** RFC 4122 v4:version 位固定为 4,variant 位为 10(即第 19 位首字符 ∈ [89ab])。 */
const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// 模块加载时先固化原生实现,供 stub 后的兜底分支复用(getRandomValues 不受安全上下文限制)。
const nativeGetRandomValues = crypto.getRandomValues.bind(crypto);

/**
 * 模拟 HTTP 非安全上下文(MES-129 故障现场):
 * `crypto.randomUUID` 缺失,但 `getRandomValues` 依旧可用。
 */
function stubNonSecureContext(getRandomValues: (array: Uint8Array) => Uint8Array): void {
  vi.stubGlobal('crypto', { getRandomValues });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('uuidv4(MES-129:安全上下文无关的 UUID v4 生成器)', () => {
  it('安全上下文(crypto.randomUUID 可用)优先复用原生实现', () => {
    const randomUUID = vi.fn(() => 'native-uuid');
    vi.stubGlobal('crypto', { randomUUID, getRandomValues: nativeGetRandomValues });

    expect(uuidv4()).toBe('native-uuid');
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it('非安全上下文(randomUUID 缺失)走 getRandomValues 兜底生成合法 v4', () => {
    const getRandomValues = vi.fn(nativeGetRandomValues);
    stubNonSecureContext(getRandomValues);

    const value = uuidv4();

    expect(value).toMatch(UUID_V4_RE);
    expect(getRandomValues).toHaveBeenCalledOnce();
  });

  it('兜底分支强制 version=4 / variant=10 位(全 0xff 字节输入)', () => {
    stubNonSecureContext((array: Uint8Array) => {
      array.fill(0xff);
      return array;
    });

    expect(uuidv4()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff');
  });

  it('兜底分支对全零字节输入同样正确', () => {
    stubNonSecureContext((array: Uint8Array) => array); // Uint8Array 默认全零

    expect(uuidv4()).toBe('00000000-0000-4000-8000-000000000000');
  });

  it('兜底分支连续 1000 次调用无重复', () => {
    stubNonSecureContext(nativeGetRandomValues);

    const values = new Set(Array.from({ length: 1000 }, () => uuidv4()));

    expect(values.size).toBe(1000);
  });
});
