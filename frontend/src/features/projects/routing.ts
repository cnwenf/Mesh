import type { Membership } from '../members/types';

/**
 * 项目页以规范深链中的 workspace slug 为上下文。仅旧扁平入口
 * 没有 slug 时兼容首个 membership，避免深链误读另一工作区数据。
 */
export function resolveProjectWorkspace(
  memberships: readonly Membership[],
  workspaceSlug: string | undefined,
): Membership | null {
  if (workspaceSlug === undefined) return memberships[0] ?? null;
  return memberships.find((membership) => membership.workspace_slug === workspaceSlug) ?? null;
}

export function projectsRoute(workspaceSlug: string): string {
  return `/w/${encodeURIComponent(workspaceSlug)}/projects`;
}

export function projectRoute(workspaceSlug: string, projectId: string): string {
  return `${projectsRoute(workspaceSlug)}/${encodeURIComponent(projectId)}`;
}

export function projectSettingsRoute(workspaceSlug: string, projectId: string): string {
  return `${projectRoute(workspaceSlug, projectId)}/settings`;
}
