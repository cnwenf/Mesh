/**
 * Issue 模块类型层运行时守卫测试:isMovePreview(LOW-3 边界校验)。
 */
import { describe, expect, it } from 'vitest';
import { isMovePreview } from '../types';

const VALID = {
  issue_id: 'iss-1',
  identifier: 'WS-1',
  from_project_id: 'prj-1',
  target_project_id: 'prj-2',
  mapped_fields: [],
  cleared_fields: [],
  kept_fields: [],
};

describe('isMovePreview(422 details.preview 回显前的边界校验)', () => {
  it('accepts a structurally complete preview', () => {
    expect(isMovePreview(VALID)).toBe(true);
  });

  it('rejects non-object inputs', () => {
    expect(isMovePreview(undefined)).toBe(false);
    expect(isMovePreview(null)).toBe(false);
    expect(isMovePreview('preview')).toBe(false);
    expect(isMovePreview(42)).toBe(false);
  });

  it('rejects previews missing required string/array members', () => {
    expect(isMovePreview({ ...VALID, issue_id: 123 })).toBe(false);
    expect(isMovePreview({ ...VALID, identifier: null })).toBe(false);
    expect(isMovePreview({ ...VALID, mapped_fields: 'none' })).toBe(false);
    expect(isMovePreview({ ...VALID, cleared_fields: { field: 'status' } })).toBe(false);
  });
});
