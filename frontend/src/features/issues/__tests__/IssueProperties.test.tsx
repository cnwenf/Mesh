import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import { I18nProvider } from '../../../i18n';
import type { MemberSummary } from '../../members/types';
import { IssueProperties } from '../IssueProperties';
import type { IssueDetail, IssueStatusRef } from '../types';

vi.mock('../../labels/IssueLabelsEditor', () => ({
  IssueLabelsEditor: () => <div data-testid="labels-editor" />,
}));
vi.mock('../../labels/IssueCustomFieldsEditor', () => ({
  IssueCustomFieldsEditor: () => <div data-testid="custom-fields-editor" />,
}));
vi.mock('../../integrations/VcsLinksPanel', () => ({
  VcsLinksPanel: () => <div data-testid="vcs-links" />,
}));
vi.mock('../IssueSquadAssignment', () => ({
  IssueSquadAssignment: () => <div data-testid="squad-assignment" />,
}));

const TODO: IssueStatusRef = {
  id: 'st-todo',
  project_id: null,
  name: 'Todo',
  category: 'todo',
  color: '#3e63dd',
  position: 1,
  is_default: true,
  allowed_transitions: ['st-done'],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const STATUSES: readonly IssueStatusRef[] = [
  TODO,
  { ...TODO, id: 'st-wip', name: 'In progress', category: 'in_progress', is_default: false },
  { ...TODO, id: 'st-done', name: 'Done', category: 'done', is_default: false },
];

const MEMBERS: readonly MemberSummary[] = [
  {
    id: 'mem-human',
    member_type: 'human',
    role: 'member',
    status: 'active',
    display_name: 'Human',
    joined_at: null,
    profile: null,
  },
  {
    id: 'mem-agent',
    member_type: 'agent',
    role: 'member',
    status: 'active',
    display_name: 'Builder',
    joined_at: null,
    profile: null,
  },
];

const ISSUE: IssueDetail = {
  id: 'iss-1',
  workspace_id: 'ws-1',
  project_id: null,
  project: null,
  identifier_namespace_key: 'WS',
  number: 1,
  identifier: 'WS-1',
  title: 'Issue',
  description: null,
  status: TODO,
  status_id: TODO.id,
  state_category: 'todo',
  priority: 'medium',
  assignee: { id: 'mem-human', name: 'Human', member_type: 'human' },
  assignee_id: 'mem-human',
  reporter: null,
  reporter_id: null,
  estimate: null,
  estimate_unit: null,
  due_date: null,
  start_date: null,
  milestone_id: null,
  cycle_id: null,
  parent_id: null,
  position: 0,
  completed_at: null,
  version: 3,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
  children_progress: { total: 0, done: 0 },
};

type PropertiesOverrides = Partial<React.ComponentProps<typeof IssueProperties>>;

function propertiesTree(overrides: PropertiesOverrides, onPatch: ReturnType<typeof vi.fn>) {
  return (
    <I18nProvider
      workspaceDefaultLocale={null}
      reporter={{ report: () => undefined, reported: [] }}
    >
      <IssueProperties
        workspaceSlug="team"
        issue={ISSUE}
        statuses={STATUSES}
        members={MEMBERS}
        projects={[]}
        milestones={[]}
        cycles={[]}
        client={{} as MeshApiClient}
        realtime={null}
        reloadKey={0}
        statusStrictMode={false}
        statusValidationError={null}
        onPatch={onPatch}
        onRequestMove={vi.fn()}
        onIssueChanged={vi.fn()}
        {...overrides}
      />
    </I18nProvider>
  );
}

function renderProperties(overrides: PropertiesOverrides = {}) {
  const onPatch = vi.fn();
  const view = render(propertiesTree(overrides, onPatch));
  return {
    ...view,
    onPatch,
    rerenderProperties: (next: PropertiesOverrides) =>
      view.rerender(propertiesTree({ ...overrides, ...next }, onPatch)),
  };
}

describe('IssueProperties status and assignee guards', () => {
  it('disables illegal strict-mode targets and explains why', () => {
    renderProperties({ statusStrictMode: true });

    expect(screen.getByRole('option', { name: 'Todo' })).not.toBeDisabled();
    expect(screen.getByRole('option', { name: 'Done' })).not.toBeDisabled();
    expect(screen.getByRole('option', { name: 'In progress' })).toBeDisabled();
    expect(screen.getByRole('option', { name: 'In progress' })).toHaveAttribute(
      'title',
      'This transition is unavailable in strict mode',
    );
    expect(screen.getByTestId('issue-status-strict-hint')).toHaveTextContent('Strict mode is on');
  });

  it('shows missing required fields beside the status control', () => {
    renderProperties({ statusValidationError: 'Missing required fields: Acceptance owner' });
    expect(screen.getByTestId('issue-status-validation-error')).toHaveTextContent(
      'Acceptance owner',
    );
  });

  it('does not patch the same assignee', () => {
    const { onPatch } = renderProperties();
    const select = screen.getByTestId('issue-detail-assignee');

    fireEvent.change(select, { target: { value: 'mem-human' } });
    expect(onPatch).not.toHaveBeenCalled();
  });

  it('shows the automation hint when the current assignee is already an agent', () => {
    renderProperties({
      issue: {
        ...ISSUE,
        assignee_id: 'mem-agent',
        assignee: { id: 'mem-agent', name: 'Builder', member_type: 'agent' },
      },
    });

    expect(screen.getByTestId('issue-agent-assignee-hint')).toHaveTextContent(
      'Work will start automatically after saving',
    );
  });

  it('derives the agent hint from controlled issue state so a failed save rollback removes it', () => {
    const { onPatch, rerenderProperties } = renderProperties();
    const select = screen.getByTestId('issue-detail-assignee');

    fireEvent.change(select, { target: { value: 'mem-agent' } });
    expect(onPatch).toHaveBeenCalledWith({ assignee_id: 'mem-agent', version: 3 });
    rerenderProperties({ issue: { ...ISSUE, assignee_id: 'mem-agent' } });
    expect(screen.getByTestId('issue-agent-assignee-hint')).toHaveTextContent(
      'Work will start automatically after saving',
    );

    rerenderProperties({ issue: ISSUE });
    expect(screen.queryByTestId('issue-agent-assignee-hint')).not.toBeInTheDocument();
  });
});
