/**
 * ReactionBar 组件测试(comment-inbox.md §4.1 第 4 点):chip 切换、选择器开合、选 emoji。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { ReactionBar } from '../ReactionBar';
import type { ReactionSummary } from '../types';

const REACTIONS: ReactionSummary[] = [
  { emoji: '👍', count: 2, reacted_by_me: true, actors: [{ id: 'm', member_type: 'human', name: 'A' }] },
];

describe('ReactionBar', () => {
  it('toggles an existing reaction chip', () => {
    const onToggle = vi.fn();
    renderWithProviders(<ReactionBar reactions={REACTIONS} onToggle={onToggle} onAdd={vi.fn()} />);
    fireEvent.click(screen.getByTestId('reaction-👍'));
    expect(onToggle).toHaveBeenCalledWith('👍');
  });

  it('opens the picker and adds an emoji', () => {
    const onAdd = vi.fn();
    renderWithProviders(<ReactionBar reactions={[]} onToggle={vi.fn()} onAdd={onAdd} />);
    expect(screen.queryByTestId('reaction-picker')).toBeNull();
    fireEvent.click(screen.getByTestId('reaction-add'));
    expect(screen.getByTestId('reaction-picker')).toBeTruthy();
    fireEvent.click(screen.getByTestId('reaction-pick-🎉'));
    expect(onAdd).toHaveBeenCalledWith('🎉');
    // picker closes after a pick
    expect(screen.queryByTestId('reaction-picker')).toBeNull();
  });

  it('reflects reacted_by_me via aria-pressed', () => {
    renderWithProviders(<ReactionBar reactions={REACTIONS} onToggle={vi.fn()} onAdd={vi.fn()} />);
    expect(screen.getByTestId('reaction-👍').getAttribute('aria-pressed')).toBe('true');
  });
});
