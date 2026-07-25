import { describe, expect, it } from 'vitest';
import { MeshApiError, errorToI18nKey } from '../errors';

describe('MeshApiError(README §6.14 错误归一)', () => {
  it('保存 status/code/message 并继承 Error', () => {
    // Arrange / Act
    const err = new MeshApiError({ status: 404, code: 'not_found', message: 'missing' });

    // Assert
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(MeshApiError);
    expect(err.name).toBe('MeshApiError');
    expect(err.status).toBe(404);
    expect(err.code).toBe('not_found');
    expect(err.message).toBe('missing');
    expect(err.details).toBeUndefined();
    expect(err.retryAfter).toBeUndefined();
  });

  it('保存可选 details 与 retryAfter', () => {
    // Arrange
    const details = { field: 'title' };

    // Act
    const err = new MeshApiError({
      status: 429,
      code: 'rate_limited',
      message: 'slow down',
      details,
      retryAfter: 30,
    });

    // Assert
    expect(err.details).toEqual({ field: 'title' });
    expect(err.retryAfter).toBe(30);
  });
});

describe('errorToI18nKey(README §6.18)', () => {
  it('返回 error.<code> 键,本地化文案归消息目录', () => {
    // Arrange
    const err = new MeshApiError({ status: 409, code: 'conflict', message: 'conflict' });

    // Act / Assert
    expect(errorToI18nKey(err)).toBe('error.conflict');
  });
});
