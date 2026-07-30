/**
 * scrollToAndHighlight 单测(design-quality.md §9.5.5):滚动居中 + 高亮类、
 * reduced-motion 降级、null 无操作、幂等。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HIGHLIGHT_CLASS, scrollToAndHighlight } from '../scrollToAndHighlight';

function makeElement(): HTMLElement {
  const element = document.createElement('article');
  element.scrollIntoView = vi.fn();
  document.body.appendChild(element);
  return element;
}

const noMotion = (): boolean => false;
const reducedMotion = (): boolean => true;

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('scrollToAndHighlight', () => {
  it('is a no-op for a null element', () => {
    expect(() => scrollToAndHighlight(null, { prefersReducedMotion: noMotion })).not.toThrow();
  });

  it('scrolls to center with smooth behavior and adds the highlight class', () => {
    const element = makeElement();
    scrollToAndHighlight(element, { prefersReducedMotion: noMotion });
    expect(element.scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' });
    expect(element.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
  });

  it('uses auto behavior under reduced motion (still adds the class)', () => {
    const element = makeElement();
    scrollToAndHighlight(element, { prefersReducedMotion: reducedMotion });
    expect(element.scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'auto' });
    expect(element.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
  });

  it('defaults reduced-motion detection to matchMedia when not injected', () => {
    const element = makeElement();
    scrollToAndHighlight(element);
    // vitest.setup 的 matchMedia 桩 matches=false → smooth
    expect(element.scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' });
  });

  it('is idempotent when called repeatedly', () => {
    const element = makeElement();
    scrollToAndHighlight(element, { prefersReducedMotion: noMotion });
    scrollToAndHighlight(element, { prefersReducedMotion: noMotion });
    expect(element.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
    expect(element.scrollIntoView).toHaveBeenCalledTimes(2);
  });
});
