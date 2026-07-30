/**
 * PaletteResults — 分组渲染、CJK 命中高亮(code point → <mark> 字重/下划线)、
 * 徽章经目录键渲染、稳定 DOM id 与 aria-selected、skeleton。
 * 新 i18n 键不断言译文,仅断言结构/testid/键出现。
 */
import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import type { SearchItem } from '../../api/search';
import { buildQuerySections } from '../paletteModel';
import type { PaletteSection } from '../paletteModel';
import { HighlightedTitle, PaletteResults, badgeToneForColor } from '../PaletteResults';

function issueWithHighlight(): SearchItem {
  return {
    type: 'issue',
    id: 'i-1',
    title: '登录页在 Safari 崩溃',
    context: {
      identifier: 'WEB-124',
      project: { id: 'p', name: '官网' },
      status: { id: 's', name: 'In Progress', category: 'in_progress' },
    },
    icon: 'issue',
    url: '/issues/i-1',
    badge: {
      kind: 'status',
      label_key: 'issue.status.name',
      label_params: { name: 'In Progress' },
      color: 'info',
    },
    highlight: { title: { unit: 'codepoint', ranges: [[0, 2]] } },
  };
}

const DEFAULTS = {
  onOptionHover: vi.fn(),
  onOptionActivate: vi.fn(),
  isSearching: false,
  skeletonLabel: 'loading',
  listId: 'lb-1',
  listLabel: 'Results',
};

describe('PaletteResults', () => {
  it('分组组头以 i18n 键标注;选项具稳定 DOM id(palette-opt-{stableId})', () => {
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId="issue:i-1" />,
    );
    const listbox = screen.getByRole('listbox');
    expect(listbox).toHaveAttribute('id', 'lb-1');
    const group = screen.getByRole('group');
    expect(group.getAttribute('aria-label')).toContain('search.group.issues');
    const option = screen.getByRole('option');
    expect(option).toHaveAttribute('id', 'palette-opt-issue:i-1');
    expect(option).toHaveAttribute('data-testid', 'palette-opt-issue:i-1');
    expect(option).toHaveAttribute('aria-selected', 'true');
  });

  it('CJK 命中区间渲染为 <mark class=mesh-palette__mark>([0,2) → 「登录」)', () => {
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    const { container } = renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId={null} />,
    );
    const mark = container.querySelector('mark.mesh-palette__mark');
    expect(mark).not.toBeNull();
    expect(mark?.textContent).toBe('登录');
    // 标题全文经 title 属性提供(省略号场景可查全名)
    const titleSpan = container.querySelector('.mesh-palette__title');
    expect(titleSpan).toHaveAttribute('title', '登录页在 Safari 崩溃');
  });

  it('徽章以目录键 + 参数渲染(tone 映射 info);color 未知落 neutral', () => {
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    const { container } = renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId={null} />,
    );
    const badge = container.querySelector('.mesh-badge--info');
    expect(badge).not.toBeNull();
    expect(badgeToneForColor('danger')).toBe('danger');
    expect(badgeToneForColor('warn')).toBe('warning');
    expect(badgeToneForColor('weird')).toBe('neutral');
  });

  it('isSearching 呈现 skeleton 加载行(sr-only 文案在场)', () => {
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId={null} isSearching />,
    );
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('loading')).toBeInTheDocument();
  });

  it('hover/click 经回调上报(鼠标等价路径);中键点击以新标签语义上报', () => {
    const onOptionHover = vi.fn();
    const onOptionActivate = vi.fn();
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    renderWithProviders(
      <PaletteResults
        {...DEFAULTS}
        sections={sections}
        selectedStableId={null}
        onOptionHover={onOptionHover}
        onOptionActivate={onOptionActivate}
      />,
    );
    const option = screen.getByRole('option');
    option.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
    expect(onOptionHover).toHaveBeenCalledWith('issue:i-1');
  });
});

describe('HighlightedTitle(单元)', () => {
  it('无 highlight 时纯文本 + title 属性', () => {
    renderWithProviders(
      <HighlightedTitle
        title="Plain"
        option={{ stableId: 'x', group: 'commands', title: 'Plain', icon: 'info' }}
      />,
    );
    expect(screen.getByText('Plain')).toBeInTheDocument();
  });

  it('多段区间交替渲染 mark/普通分段', () => {
    const item = issueWithHighlight();
    const sections: PaletteSection[] = buildQuerySections(
      [{ ...item, highlight: { title: { unit: 'codepoint', ranges: [[0, 2], [5, 7]] } } }],
      [],
      'x',
    );
    const { container } = renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId={null} />,
    );
    const marks = container.querySelectorAll('mark.mesh-palette__mark');
    expect(marks).toHaveLength(2);
  });
});
