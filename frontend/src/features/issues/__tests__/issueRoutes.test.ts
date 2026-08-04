import { describe, expect, it } from 'vitest';
import {
  workspaceIssueByIdentifierPath,
  workspaceIssuePath,
  workspaceIssuesPath,
  workspaceSquadPath,
} from '../issueRoutes';

describe('issue workspace routes', () => {
  it('keeps list, detail, identifier and related squad links inside the workspace', () => {
    expect(workspaceIssuesPath('team one')).toBe('/w/team%20one/issues');
    expect(workspaceIssuePath('team one', 'iss/1')).toBe('/w/team%20one/issues/iss%2F1');
    expect(workspaceIssueByIdentifierPath('team one', 'web-1')).toBe(
      '/w/team%20one/issues/by-identifier/WEB-1',
    );
    expect(workspaceSquadPath('team one', 'sq/1')).toBe('/w/team%20one/squads/sq%2F1');
  });

  it('rejects a missing workspace slug instead of producing a flat or broken link', () => {
    expect(() => workspaceIssuesPath(undefined)).toThrow('require a workspace slug');
    expect(() => workspaceIssuesPath('')).toThrow('require a workspace slug');
  });
});
