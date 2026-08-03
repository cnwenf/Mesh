/**
 * Native and ARIA widgets that can receive a pointer activation. `[tabindex]`
 * and `[contenteditable]` cover keyboard-enabled custom controls whose author
 * forgot (or cannot use) one of the widget roles.
 */
export const TOUCH_TARGET_SELECTOR = [
  'a[href]',
  'area[href]',
  'button',
  'input:not([type="hidden"])',
  'select',
  'textarea',
  'summary',
  '[contenteditable]:not([contenteditable="false"])',
  '[tabindex]:not([tabindex="-1"])',
  '[role="button"]',
  '[role="checkbox"]',
  '[role="combobox"]',
  '[role="gridcell"]',
  '[role="link"]',
  '[role="menuitem"]',
  '[role="menuitemcheckbox"]',
  '[role="menuitemradio"]',
  '[role="option"]',
  '[role="radio"]',
  '[role="scrollbar"]',
  '[role="searchbox"]',
  '[role="slider"]',
  '[role="spinbutton"]',
  '[role="switch"]',
  '[role="tab"]',
  '[role="textbox"]',
  '[role="treeitem"]',
].join(', ');

export interface TouchTargetViolation {
  readonly target: string;
  readonly width: number;
  readonly height: number;
}

/**
 * Runs in the browser through Playwright's evaluateAll, so keep the implementation
 * self-contained rather than closing over module state.
 */
export function findTouchTargetViolations(elements: Element[]): TouchTargetViolation[] {
  const floor = 44;
  const tolerance = 0.5;

  type Rect = Pick<DOMRect, 'left' | 'top' | 'right' | 'bottom' | 'width' | 'height'>;
  interface MeasuredTarget {
    readonly target: string;
    readonly rect: Rect;
  }

  const renderedRect = (node: HTMLElement): Rect | null => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    if (
      style.display === 'none' ||
      style.visibility === 'hidden' ||
      style.visibility === 'collapse' ||
      Number(style.opacity) === 0 ||
      rect.width === 0 ||
      rect.height === 0 ||
      node.closest('[inert]') !== null
    ) {
      return null;
    }
    return rect;
  };

  const targetName = (node: HTMLElement): string => {
    if (node.dataset.testid !== undefined) return node.dataset.testid;
    if (node.id !== '') return `#${node.id}`;
    const role = node.getAttribute('role');
    if (role !== null) return `${node.tagName.toLowerCase()}[role=${role}]`;
    if (node instanceof HTMLInputElement) return `input[type=${node.type}]`;
    return node.tagName.toLowerCase();
  };

  const measured = elements.flatMap<MeasuredTarget>((element) => {
    const node = element as HTMLElement;
    if (node.matches(':disabled, [aria-disabled="true"]')) return [];

    let rect = renderedRect(node);
    if (node instanceof HTMLInputElement && (node.type === 'checkbox' || node.type === 'radio')) {
      // The associated label is part of a checkbox/radio's activation target.
      // Measure it instead of silently dropping the native control when the
      // visual input is deliberately small or visually hidden.
      const labelRects = Array.from(node.labels ?? [])
        .map((label) => renderedRect(label))
        .filter((labelRect): labelRect is Rect => labelRect !== null);
      const largestLabel = labelRects.sort(
        (left, right) => right.width * right.height - left.width * left.height,
      )[0];
      if (
        largestLabel !== undefined &&
        (rect === null || largestLabel.width * largestLabel.height > rect.width * rect.height)
      ) {
        rect = largestLabel;
      }
    }

    return rect === null ? [] : [{ target: targetName(node), rect }];
  });

  return measured.flatMap<TouchTargetViolation>((candidate) => {
    if (candidate.rect.width + tolerance >= floor && candidate.rect.height + tolerance >= floor) {
      return [];
    }

    return [
      {
        target: candidate.target,
        width: Math.round(candidate.rect.width),
        height: Math.round(candidate.rect.height),
      },
    ];
  });
}
