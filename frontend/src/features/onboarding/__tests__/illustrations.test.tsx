/**
 * 插画组件冒烟测试(onboarding.md §1.2.2/§4.2):装饰性 SVG,aria-hidden + role presentation。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  AhaCelebration,
  EmptyAutomation,
  EmptyBoardColumns,
  EmptyChatBubbles,
  EmptyFolder,
  EmptyInboxTray,
  EmptyRoster,
} from '../illustrations';

const CASES = [
  ['EmptyInboxTray', EmptyInboxTray, 'illustration-inbox-tray'],
  ['EmptyFolder', EmptyFolder, 'illustration-folder'],
  ['EmptyBoardColumns', EmptyBoardColumns, 'illustration-board'],
  ['EmptyRoster', EmptyRoster, 'illustration-roster'],
  ['EmptyChatBubbles', EmptyChatBubbles, 'illustration-chat'],
  ['EmptyAutomation', EmptyAutomation, 'illustration-automation'],
  ['AhaCelebration', AhaCelebration, 'illustration-aha'],
] as const;

describe.each(CASES)('%s', (_name, Component, testId) => {
  it('renders a decorative svg hidden from assistive tech', () => {
    render(<Component />);
    const svg = screen.getByTestId(testId);
    expect(svg.tagName.toLowerCase()).toBe('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg).toHaveAttribute('role', 'presentation');
    expect(svg).toHaveAttribute('width', '120');
  });
});
