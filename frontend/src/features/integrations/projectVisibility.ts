import type { MeshApiClient } from '../../api';
import { listProjects } from '../projects/api';
import type { ProjectSummary } from '../projects/types';

const PROJECT_PAGE_LIMIT = 100;

/** Load every project the current member may see, including archived projects. */
export async function listAllVisibleProjects(
  client: MeshApiClient,
  workspaceId: string,
): Promise<ReadonlyArray<ProjectSummary>> {
  const projects = new Map<string, ProjectSummary>();

  for (const archived of [false, true]) {
    let cursor: string | undefined;
    do {
      const page = await listProjects(client, workspaceId, {
        archived,
        cursor,
        limit: PROJECT_PAGE_LIMIT,
      });
      page.data.forEach((project) => projects.set(project.id, project));
      cursor = page.nextCursor ?? undefined;
    } while (cursor !== undefined);
  }

  return [...projects.values()];
}
