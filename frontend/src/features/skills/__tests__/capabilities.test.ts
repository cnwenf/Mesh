import { describe, expect, it } from 'vitest';
import { effectiveCapabilities, normalizeCapability, permissionTone } from '../capabilities';

describe('capability declaration presentation', () => {
  it('normalizes bare declarations to confirm_required', () => {
    expect(normalizeCapability('exec:shell')).toEqual({
      capability: 'exec:shell',
      permission: 'confirm_required',
    });
  });

  it('keeps an explicit permission', () => {
    expect(normalizeCapability({ capability: 'repo:read', permission: 'read_only' })).toEqual({
      capability: 'repo:read',
      permission: 'read_only',
    });
  });

  it('deduplicates effective grants and keeps the stricter permission', () => {
    expect(
      effectiveCapabilities([
        { capability: 'repo:read', permission: 'read_only' },
        { capability: 'repo:read', permission: 'write' },
        'exec:shell',
      ]),
    ).toEqual([
      { capability: 'exec:shell', permission: 'confirm_required' },
      { capability: 'repo:read', permission: 'write' },
    ]);
  });

  it('maps every permission to a non-color-only presentation tone', () => {
    expect(permissionTone('read_only')).toBe('neutral');
    expect(permissionTone('write')).toBe('danger');
    expect(permissionTone('confirm_required')).toBe('warning');
  });
});
