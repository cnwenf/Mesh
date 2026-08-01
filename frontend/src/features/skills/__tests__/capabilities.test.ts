import { describe, expect, it } from 'vitest';
import {
  effectiveCapabilities,
  effectiveGrants,
  normalizeCapability,
  permissionTone,
} from '../capabilities';
import type { CapabilityDeclaration, CapabilityGrant } from '../types';

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

  it('grant normalization excludes explicitly disabled grants', () => {
    expect(
      effectiveGrants([
        { capability: 'exec:shell', permission: 'confirm_required', enabled: false },
        { capability: 'repo:read', permission: 'read_only', enabled: true },
        'issue:read',
      ]),
    ).toEqual([
      { capability: 'issue:read', permission: 'confirm_required' },
      { capability: 'repo:read', permission: 'read_only' },
    ]);
  });

  it('keeps enabled state exclusive to grant declarations', () => {
    const grant: CapabilityGrant = { capability: 'exec:shell', enabled: false };
    // @ts-expect-error required declarations intentionally have no runtime enabled state.
    const required: CapabilityDeclaration = { capability: 'exec:shell', enabled: false };
    expect(grant).toHaveProperty('enabled', false);
    expect(required).toHaveProperty('enabled', false);
  });

  it('maps every permission to a non-color-only presentation tone', () => {
    expect(permissionTone('read_only')).toBe('neutral');
    expect(permissionTone('write')).toBe('danger');
    expect(permissionTone('confirm_required')).toBe('warning');
  });
});
