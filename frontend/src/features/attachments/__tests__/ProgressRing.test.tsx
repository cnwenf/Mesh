/**
 * ProgressRing 单测(§3.2 进度环):渲染/ARIA/百分比文本/钳制/不确定态。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { clampPercent, ProgressRing } from '../components/ProgressRing';

describe('ProgressRing', () => {
  it('renders a progressbar with value and percent text', () => {
    render(<ProgressRing value={42} label="Uploading a.png" />);
    const ring = screen.getByRole('progressbar', { name: 'Uploading a.png' });
    expect(ring.getAttribute('aria-valuenow')).toBe('42');
    expect(ring.getAttribute('aria-valuemin')).toBe('0');
    expect(ring.getAttribute('aria-valuemax')).toBe('100');
    expect(screen.getByTestId('progress-ring-text').textContent).toBe('42%');
  });

  it('clamps out-of-range values into 0–100', () => {
    const { rerender } = render(<ProgressRing value={150} label="x" />);
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100');
    rerender(<ProgressRing value={-20} label="x" />);
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('0');
  });

  it('renders the indeterminate state without a value or percent text', () => {
    render(<ProgressRing value={0} indeterminate label="Checking file" />);
    const ring = screen.getByRole('progressbar', { name: 'Checking file' });
    expect(ring.getAttribute('aria-valuenow')).toBeNull();
    expect(ring.classList.contains('mesh-progress-ring--indeterminate')).toBe(true);
    expect(screen.queryByTestId('progress-ring-text')).toBeNull();
  });

  it('uses tabular numerals for the percent text', () => {
    render(<ProgressRing value={7} label="x" />);
    expect(screen.getByTestId('progress-ring-text').classList.contains('mesh-tnum')).toBe(true);
  });
});

describe('clampPercent', () => {
  it('clamps, and falls back to 0 for non-finite input', () => {
    expect(clampPercent(50)).toBe(50);
    expect(clampPercent(101)).toBe(100);
    expect(clampPercent(-1)).toBe(0);
    expect(clampPercent(Number.NaN)).toBe(0);
    expect(clampPercent(Number.POSITIVE_INFINITY)).toBe(0);
  });
});
