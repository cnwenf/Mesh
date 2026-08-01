import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import { MemberAvatarWall } from '../MemberAvatarWall';
import { SquadTaskKanban } from '../SquadTaskKanban';
import type { MemberPreview, SquadTask, SquadTaskStatus } from '../types';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

function ToastLayer(props: { children: React.ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

function renderDesign(child: React.ReactNode): void {
  render(
    <ThemeProvider>
      <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
        <ToastLayer>{child}</ToastLayer>
      </I18nProvider>
    </ThemeProvider>,
  );
}

function task(id: string, status: SquadTaskStatus, overrides: Partial<SquadTask> = {}): SquadTask {
  return {
    id,
    squad_id: 'sq-1',
    issue_id: `issue-${id}`,
    parent_task_id: 'root',
    root_task_id: 'root',
    depth: 1,
    title_snapshot: `Task ${id}`,
    status,
    assignee: null,
    stage: null,
    execution_id: null,
    plan_markdown: null,
    result_summary: null,
    failure_reason: null,
    depends_on: [],
    blocked_by: [],
    dispatched_at: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

describe('MemberAvatarWall', () => {
  it('handles an omitted list and renders human, agent, leader, initials, and limit states', () => {
    const { rerender } = render(
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <MemberAvatarWall members={undefined} />
        </I18nProvider>
      </ThemeProvider>,
    );
    expect(screen.getByText('No members yet')).toBeInTheDocument();

    const members: MemberPreview[] = [
      { member_id: 'agent', member_type: 'agent', name: ' builder', role: 'leader' },
      { member_id: 'human', member_type: 'human', name: '   ', role: 'member' },
      { member_id: 'hidden', member_type: 'human', name: 'Hidden', role: 'observer' },
    ];
    rerender(
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <MemberAvatarWall members={members} limit={2} />
        </I18nProvider>
      </ThemeProvider>,
    );

    expect(screen.getByTestId('squad-avatar-agent')).toHaveTextContent('B');
    expect(screen.getByTestId('squad-avatar-human')).toHaveTextContent('?');
    expect(screen.getByTestId('squad-avatar-leader-agent')).toBeInTheDocument();
    expect(screen.queryByTestId('squad-avatar-hidden')).toBeNull();
  });
});

describe('SquadTaskKanban', () => {
  it('renders all card signals and handles drag lifecycle and every valid destination', async () => {
    const tasks = [
      task('fallback', 'decomposing', { title_snapshot: null }),
      task('dispatching', 'dispatching'),
      task('progress', 'in_progress', {
        assignee: { member_id: 'human', member_type: 'human', name: 'Owner' },
        stage: 2,
      }),
      task('blocked', 'blocked', {
        assignee: { member_id: 'agent', member_type: 'agent', name: 'Builder' },
        blocked_by: ['done', 'missing'],
        depends_on: ['done', 'missing'],
        failure_reason: 'Waiting for capacity',
      }),
      task('done', 'done'),
      task('failed', 'failed', { failure_reason: '' }),
      task('cancelled', 'cancelled'),
    ];
    const index = new Map(tasks.map((item) => [item.id, item]));
    const onMoveTask = vi.fn().mockResolvedValue(undefined);
    renderDesign(<SquadTaskKanban tasks={tasks} index={index} onMoveTask={onMoveTask} />);

    expect(screen.getByTestId('squad-kanban-card-fallback')).toHaveTextContent('fallback');
    expect(screen.getByTestId('squad-kanban-card-progress')).toHaveTextContent('Owner');
    expect(screen.getByTestId('squad-kanban-card-progress')).toHaveTextContent('Stage 2');
    expect(screen.getByTestId('squad-kanban-card-blocked')).toHaveTextContent('Builder');
    expect(screen.getByTestId('squad-kanban-blocked-blocked')).toHaveTextContent('Task done');
    expect(screen.getByTestId('squad-kanban-card-blocked')).toHaveTextContent('missing');
    expect(screen.getByTestId('squad-kanban-card-blocked')).toHaveTextContent(
      'Waiting for capacity',
    );

    const transfer = { setData: vi.fn(), effectAllowed: '' };
    fireEvent.dragStart(screen.getByTestId('squad-kanban-card-progress'), {
      dataTransfer: transfer,
    });
    expect(transfer.setData).toHaveBeenCalledWith('text/mesh-squad-task-id', 'progress');
    expect(transfer.effectAllowed).toBe('move');

    fireEvent.dragOver(screen.getByTestId('squad-kanban-col-pending'));
    expect(screen.getByTestId('squad-kanban-col-pending')).toHaveClass(
      'mesh-squads__kanban-col--over',
    );
    fireEvent.dragLeave(screen.getByTestId('squad-kanban-col-done'));
    expect(screen.getByTestId('squad-kanban-col-pending')).toHaveClass(
      'mesh-squads__kanban-col--over',
    );
    fireEvent.dragLeave(screen.getByTestId('squad-kanban-col-pending'));
    expect(screen.getByTestId('squad-kanban-col-pending')).not.toHaveClass(
      'mesh-squads__kanban-col--over',
    );

    fireEvent.drop(screen.getByTestId('squad-kanban-col-pending'), {
      dataTransfer: { getData: () => 'blocked' },
    });
    fireEvent.drop(screen.getByTestId('squad-kanban-col-blocked'), {
      dataTransfer: { getData: () => 'progress' },
    });
    fireEvent.drop(screen.getByTestId('squad-kanban-col-done'), {
      dataTransfer: { getData: () => 'progress' },
    });
    fireEvent.drop(screen.getByTestId('squad-kanban-col-failed'), {
      dataTransfer: { getData: () => 'progress' },
    });
    await waitFor(() => expect(onMoveTask).toHaveBeenCalledTimes(4));
    expect(onMoveTask.mock.calls).toEqual([
      ['blocked', 'in_progress'],
      ['progress', 'blocked'],
      ['progress', 'done'],
      ['progress', 'failed'],
    ]);
  });

  it('ignores empty, unknown, unchanged, and invalid drops', async () => {
    const done = task('done', 'done');
    const index = new Map([[done.id, done]]);
    const onMoveTask = vi.fn().mockResolvedValue(undefined);
    renderDesign(<SquadTaskKanban tasks={[done]} index={index} onMoveTask={onMoveTask} />);

    fireEvent.drop(screen.getByTestId('squad-kanban-col-done'), {
      dataTransfer: { getData: () => '' },
    });
    fireEvent.drop(screen.getByTestId('squad-kanban-col-done'), {
      dataTransfer: { getData: () => 'unknown' },
    });
    fireEvent.drop(screen.getByTestId('squad-kanban-col-done'), {
      dataTransfer: { getData: () => 'done' },
    });
    fireEvent.drop(screen.getByTestId('squad-kanban-col-pending'), {
      dataTransfer: { getData: () => 'done' },
    });

    expect(await screen.findByText('That move is not allowed.')).toBeInTheDocument();
    expect(onMoveTask).not.toHaveBeenCalled();
  });
});
