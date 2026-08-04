import { fireEvent, screen } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AgentDetailPage } from '../../agents/AgentDetailPage';
import { AutopilotDetailPage } from '../../autopilots/AutopilotDetailPage';
import { AutopilotsPage } from '../../autopilots/AutopilotsPage';
import { ExecutionDetailPage } from '../../runtimes/ExecutionDetailPage';
import { RuntimeDetailPage } from '../../runtimes/RuntimeDetailPage';
import { RuntimesPage } from '../../runtimes/RuntimesPage';
import { MembersPage } from '../MembersPage';

const membershipRetry = vi.hoisted(() => vi.fn());

vi.mock('../useWorkspaceMembership', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../useWorkspaceMembership')>();
  return {
    ...actual,
    useWorkspaceMembership: () => ({ kind: 'error', retry: membershipRetry }),
  };
});

interface ConsumerCase {
  readonly name: string;
  readonly render: () => ReactElement;
}

const CONSUMERS: readonly ConsumerCase[] = [
  { name: 'MembersPage', render: () => <MembersPage /> },
  { name: 'RuntimesPage', render: () => <RuntimesPage /> },
  { name: 'RuntimeDetailPage', render: () => <RuntimeDetailPage /> },
  { name: 'ExecutionDetailPage', render: () => <ExecutionDetailPage /> },
  { name: 'AgentDetailPage', render: () => <AgentDetailPage /> },
  { name: 'AutopilotsPage', render: () => <AutopilotsPage /> },
  { name: 'AutopilotDetailPage', render: () => <AutopilotDetailPage /> },
];

describe('workspace membership retry consumers', () => {
  beforeEach(() => {
    membershipRetry.mockReset();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'unexpected request' } },
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each(CONSUMERS)('$name delegates its membership ErrorState retry', async ({ render }) => {
    renderWithProviders(render());

    fireEvent.click(await screen.findByRole('button', { name: /Retry|重试/i }));

    expect(membershipRetry).toHaveBeenCalledTimes(1);
  });
});
