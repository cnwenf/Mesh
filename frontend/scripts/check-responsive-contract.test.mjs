import { describe, expect, it } from 'vitest';
import { findDisallowedViewportWidths } from './responsive-contract-parser.mjs';

const ALLOWED = new Set([599, 600, 1023, 1024, 1439, 1440]);

describe('responsive contract parser', () => {
  it('checks every min/max width in a combined media query', () => {
    const source = '@media (min-width: 720px) and (max-width: 1023px) { .x {} }';

    expect(findDisallowedViewportWidths(source, ALLOWED).map(({ value }) => value)).toEqual([720]);
  });

  it('allows centralized viewport boundaries', () => {
    const source = '@media (min-width: 600px) and (max-width: 1023px) { .x {} }';

    expect(findDisallowedViewportWidths(source, ALLOWED)).toEqual([]);
  });

  it('does not couple component container thresholds to viewport modes', () => {
    const source = '@container card (min-width: 720px) { .x {} }';

    expect(findDisallowedViewportWidths(source, ALLOWED)).toEqual([]);
  });
});
