import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { LabelDots } from '../LabelDots';
import type { CompactLabel } from '../types';

const LABELS: readonly CompactLabel[] = [
  { id: 'a', name: 'bug', color: '#e5484d' },
  { id: 'b', name: 'frontend', color: '#3e63dd' },
  { id: 'c', name: 'customer', color: '#46a758' },
  { id: 'd', name: 'urgent', color: '#f5a623' },
  { id: 'e', name: 'research', color: '#8e4ec6' },
];

function renderDots(labels = LABELS, maxVisible?: number): ReturnType<typeof render> {
  return render(
    <I18nProvider
      workspaceDefaultLocale={null}
      reporter={{ report: () => undefined, reported: [] }}
    >
      <LabelDots labels={labels} maxVisible={maxVisible} />
    </I18nProvider>,
  );
}

describe('LabelDots', () => {
  it('renders compact data-colour dots and a +N overflow counter', () => {
    renderDots();

    const dots = screen.getAllByTestId('issue-label-dot');
    expect(dots).toHaveLength(3);
    expect(dots.map((dot) => dot.style.backgroundColor)).toEqual([
      'rgb(229, 72, 77)',
      'rgb(62, 99, 221)',
      'rgb(70, 167, 88)',
    ]);
    expect(screen.getByTestId('issue-label-overflow')).toHaveTextContent('+2');
    expect(screen.getByTestId('issue-label-summary')).toHaveAccessibleName(
      'Labels: bug, frontend, customer, urgent, research',
    );
  });

  it('returns no summary for an issue without labels', () => {
    const view = renderDots([]);
    expect(view.container).toBeEmptyDOMElement();
  });

  it('omits the overflow counter when every label is visible', () => {
    renderDots(LABELS.slice(0, 2), 3);
    expect(screen.getAllByTestId('issue-label-dot')).toHaveLength(2);
    expect(screen.queryByTestId('issue-label-overflow')).not.toBeInTheDocument();
  });

  it('clamps a negative visibility limit and reports every label as overflow', () => {
    renderDots(LABELS.slice(0, 2), -1);
    expect(screen.queryByTestId('issue-label-dot')).not.toBeInTheDocument();
    expect(screen.getByTestId('issue-label-overflow')).toHaveTextContent('+2');
  });
});
