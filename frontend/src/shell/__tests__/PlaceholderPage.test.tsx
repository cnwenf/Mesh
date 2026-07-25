/**
 * PlaceholderPage — 各占位路由呈现对应空态(标题取 nav.<kind>)。
 */
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { PlaceholderPage } from '../PlaceholderPage';
import type { PlaceholderKind } from '../PlaceholderPage';

const CASES: ReadonlyArray<{ kind: PlaceholderKind; label: string }> = [
  { kind: 'inbox', label: 'Inbox' },
  { kind: 'projects', label: 'Projects' },
  { kind: 'board', label: 'Board' },
  { kind: 'members', label: 'Members' },
  { kind: 'chat', label: 'Chat' },
  { kind: 'automation', label: 'Automation' },
];

describe('PlaceholderPage', () => {
  it.each(CASES)('kind=$kind 呈现对应空态标题与通用描述', ({ kind, label }) => {
    renderWithProviders(<PlaceholderPage kind={kind} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText('Items you create or follow will show up here.')).toBeInTheDocument();
  });
});
