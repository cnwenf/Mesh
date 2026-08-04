/**
 * Issue 模块的工作区规范深链。所有动态段统一编码，避免列表、详情及关联资源
 * 各自拼接出扁平旧路径或未转义路径。
 */

function workspaceSegment(workspaceSlug: string | undefined): string {
  if (workspaceSlug === undefined || workspaceSlug === '') {
    throw new Error('Issue routes require a workspace slug');
  }
  return encodeURIComponent(workspaceSlug);
}

export function workspaceIssuesPath(workspaceSlug: string | undefined): string {
  return `/w/${workspaceSegment(workspaceSlug)}/issues`;
}

export function workspaceIssuePath(workspaceSlug: string | undefined, issueId: string): string {
  return `${workspaceIssuesPath(workspaceSlug)}/${encodeURIComponent(issueId)}`;
}

export function workspaceIssueByIdentifierPath(
  workspaceSlug: string | undefined,
  identifier: string,
): string {
  return `${workspaceIssuesPath(workspaceSlug)}/by-identifier/${encodeURIComponent(identifier.toUpperCase())}`;
}

export function workspaceSquadPath(workspaceSlug: string | undefined, squadId: string): string {
  return `/w/${workspaceSegment(workspaceSlug)}/squads/${encodeURIComponent(squadId)}`;
}
