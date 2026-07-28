/**
 * toErrorKey 错误归一测试(§3.4):MeshApiError 取 code 键,其余回退(默认 / 自定义)。
 */
import { describe, expect, it } from 'vitest';
import { MeshApiError } from '../../../api';
import { toErrorKey } from '../errors';

describe('toErrorKey(§3.4 错误归一)', () => {
  it('MeshApiError → error.<code>', () => {
    const err = new MeshApiError({ status: 409, code: 'generation_in_progress', message: 'busy' });
    expect(toErrorKey(err)).toBe('error.generation_in_progress');
  });

  it('非 MeshApiError → 默认通用错误键', () => {
    expect(toErrorKey(new Error('boom'))).toBe('common.unknownError');
    expect(toErrorKey('not-an-error')).toBe('common.unknownError');
  });

  it('非 MeshApiError → 自定义回退键', () => {
    expect(toErrorKey(new Error('boom'), 'state.errorDescription')).toBe('state.errorDescription');
  });
});
