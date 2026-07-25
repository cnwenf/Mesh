import { describe, expect, it } from 'vitest';
import {
  INVITATION_ROLES,
  canDeleteWorkspace,
  canManageInvitations,
  canManageMembers,
  canViewSettings,
  isHttpsUrl,
  isValidEmail,
  isValidSlug,
  roleRank,
} from '../permissions';

describe('permissions(角色裁决呈现构件,workspace.md §3.1 / README §6.12)', () => {
  it('roleRank 等级序 owner > admin > member > guest', () => {
    expect(roleRank('owner')).toBeGreaterThan(roleRank('admin'));
    expect(roleRank('admin')).toBeGreaterThan(roleRank('member'));
    expect(roleRank('member')).toBeGreaterThan(roleRank('guest'));
  });

  it('canViewSettings:admin/owner 可见,member/guest 不可见', () => {
    expect(canViewSettings('owner')).toBe(true);
    expect(canViewSettings('admin')).toBe(true);
    expect(canViewSettings('member')).toBe(false);
    expect(canViewSettings('guest')).toBe(false);
  });

  it('canManageInvitations 与 canManageMembers:admin+', () => {
    for (const role of ['owner', 'admin'] as const) {
      expect(canManageInvitations(role)).toBe(true);
      expect(canManageMembers(role)).toBe(true);
    }
    for (const role of ['member', 'guest'] as const) {
      expect(canManageInvitations(role)).toBe(false);
      expect(canManageMembers(role)).toBe(false);
    }
  });

  it('canDeleteWorkspace:仅 owner', () => {
    expect(canDeleteWorkspace('owner')).toBe(true);
    expect(canDeleteWorkspace('admin')).toBe(false);
    expect(canDeleteWorkspace('member')).toBe(false);
    expect(canDeleteWorkspace('guest')).toBe(false);
  });

  it('INVITATION_ROLES 不含 owner(不可邀请为 owner)', () => {
    expect(INVITATION_ROLES).toEqual(['admin', 'member', 'guest']);
  });

  it('isValidSlug 遵循 ^[a-z0-9-]{2,32}$', () => {
    expect(isValidSlug('acme')).toBe(true);
    expect(isValidSlug('acme-corp-2')).toBe(true);
    expect(isValidSlug('a')).toBe(false);
    expect(isValidSlug('Acme')).toBe(false);
    expect(isValidSlug('acme corp')).toBe(false);
    expect(isValidSlug('a'.repeat(33))).toBe(false);
    expect(isValidSlug('a_1')).toBe(false);
  });

  it('isValidEmail 粗校验格式', () => {
    expect(isValidEmail('jane@corp.com')).toBe(true);
    expect(isValidEmail('not-an-email')).toBe(false);
    expect(isValidEmail('a@b')).toBe(false);
    expect(isValidEmail('a b@c.com')).toBe(false);
  });

  it('isHttpsUrl 仅允许 https scheme(§6.16)', () => {
    expect(isHttpsUrl('https://cdn.example/logo.png')).toBe(true);
    expect(isHttpsUrl('http://cdn.example/logo.png')).toBe(false);
    expect(isHttpsUrl('javascript:alert(1)')).toBe(false);
    expect(isHttpsUrl('data:image/png;base64,x')).toBe(false);
  });
});
