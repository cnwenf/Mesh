/**
 * 工作区角色裁决构件(workspace.md §3.1 最低角色 / README §6.12 全局 IA)。
 *
 * 前端仅做**呈现级**门控(节区可见性、入口可用性);一切写操作的权威校验在后端
 * (auth.md RBAC),前端门控被绕过时后端以 403/404 兜底。
 */
import type { WorkspaceRole } from '../api/workspace';

/** 角色等级(auth.md RBAC:owner 3 / admin 2 / member 1 / guest 0) */
const ROLE_RANK: Readonly<Record<WorkspaceRole, number>> = {
  owner: 3,
  admin: 2,
  member: 1,
  guest: 0,
};

export function roleRank(role: WorkspaceRole): number {
  return ROLE_RANK[role];
}

/** 设置页可见角色:admin / owner(README §6.12:guest/agent/普通成员不面对管理后台) */
export function canViewSettings(role: WorkspaceRole): boolean {
  return roleRank(role) >= ROLE_RANK.admin;
}

/** 邀请管理(创建/列出/撤销)最低角色:admin(workspace.md §3.1) */
export function canManageInvitations(role: WorkspaceRole): boolean {
  return roleRank(role) >= ROLE_RANK.admin;
}

/** 成员角色变更最低角色:admin(member.md §3.1) */
export function canManageMembers(role: WorkspaceRole): boolean {
  return roleRank(role) >= ROLE_RANK.admin;
}

/** 危险操作(删除/恢复工作区):仅 owner(workspace.md §3.1,W10) */
export function canDeleteWorkspace(role: WorkspaceRole): boolean {
  return role === 'owner';
}

/** 邀请可预设角色:不可邀请为 owner(workspace.md §2.3) */
export const INVITATION_ROLES: readonly WorkspaceRole[] = ['admin', 'member', 'guest'];

/** slug 格式(workspace.md §2.2:`^[a-z0-9-]{2,32}$`) */
const SLUG_PATTERN = /^[a-z0-9-]{2,32}$/;

export function isValidSlug(slug: string): boolean {
  return SLUG_PATTERN.test(slug);
}

/** 邮箱格式粗校验(定向邀请 chip 输入;权威校验在后端) */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(email: string): boolean {
  return EMAIL_PATTERN.test(email);
}

/** logo_url 仅允许 https(README §6.16 用户可控 URL 统一 https-only) */
export function isHttpsUrl(url: string): boolean {
  return url.startsWith('https://');
}
