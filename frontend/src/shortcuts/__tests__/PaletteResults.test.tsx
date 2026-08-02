/**
 * PaletteResults — 分组渲染、CJK 命中高亮(code point → <mark> 字重/下划线)、
 * 徽章经目录键渲染、稳定 DOM id 与 aria-selected、skeleton。
 * 新 i18n 键不断言译文,仅断言结构/testid/键出现。
 */
import { fireEvent, screen, within } from '@testing-library/react';
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
  it('selectedStableId 变化时把共享结果行滚动到最近可见位置', () => {
    const scrollIntoView = vi.fn();
    const original = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      const sections = buildQuerySections([issueWithHighlight()], [], '登录');
      renderWithProviders(
        <PaletteResults {...DEFAULTS} sections={sections} selectedStableId="issue:i-1" />,
      );
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    } finally {
      if (original === undefined) {
        // @ts-expect-error jsdom 可无该 DOM API,恢复为原始缺席状态。
        delete window.HTMLElement.prototype.scrollIntoView;
      } else {
        window.HTMLElement.prototype.scrollIntoView = original;
      }
    }
  });

  it('分组组头以 i18n 键标注;选项具稳定 DOM id(palette-opt-{stableId})', () => {
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId="issue:i-1" />,
    );
    const listbox = screen.getByRole('listbox');
    expect(listbox).toHaveAttribute('id', 'lb-1');
    const group = screen.getByRole('group');
    // 组头经 labelKey 走目录解析(en 权威源语言:Issues)
    expect(group.getAttribute('aria-label')).toBe('Issues');
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
    expect(mark).toHaveClass('mesh-palette__hit');
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

  it('badgeToneForColor 全语义映射(success/accent/warning/info)', () => {
    expect(badgeToneForColor('success')).toBe('success');
    expect(badgeToneForColor('accent')).toBe('accent');
    expect(badgeToneForColor('warning')).toBe('warning');
    expect(badgeToneForColor('info')).toBe('info');
  });

  it('副标题枚举参数经目录二次解析(memberType/scope/visibility)', () => {
    const sections: ReadonlyArray<PaletteSection> = [
      {
        key: 'members',
        labelKey: 'search.group.members',
        options: [
          {
            stableId: 'member:m-1',
            group: 'members',
            title: 'Ada',
            icon: 'user',
            subtitle: {
              key: 'search.subtitle.member',
              params: { memberType: 'human', role: 'admin' },
            },
          },
        ],
      },
      {
        key: 'views',
        labelKey: 'search.group.views',
        options: [
          {
            stableId: 'view:v-1',
            group: 'views',
            title: 'Sprint view',
            icon: 'info',
            subtitle: { key: 'search.subtitle.view', params: { scope: 'project' } },
          },
        ],
      },
      {
        key: 'projects',
        labelKey: 'search.group.projects',
        options: [
          {
            stableId: 'project:p-1',
            group: 'projects',
            title: 'Web',
            icon: 'info',
            subtitle: {
              key: 'search.subtitle.project',
              params: { key: 'WEB', visibility: 'private' },
            },
          },
        ],
      },
    ];
    const { container } = renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId={null} />,
    );
    const subtitles = Array.from(container.querySelectorAll('.mesh-palette__subtitle')).map(
      (node) => node.textContent ?? '',
    );
    expect(subtitles).toHaveLength(3);
    // 枚举参数经目录解析为本地化文案(en 权威源)
    expect(subtitles[0]).toContain('Member');
    expect(subtitles[1]).toContain('Project view');
    expect(subtitles[2]).toContain('private');
  });

  it('命令选项的 combo 经 Kbd 渲染(formatCombo 平台字面)', () => {
    const sections: ReadonlyArray<PaletteSection> = [
      {
        key: 'commands',
        labelKey: 'search.group.commands',
        options: [
          { stableId: 'cmd:x', group: 'commands', title: 'Do thing', icon: 'info', combo: 'mod+k' },
        ],
      },
    ];
    renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId={null} />,
    );
    const option = screen.getByRole('option');
    // jsdom 非 mac 平台 → mod 渲染为 Ctrl
    expect(within(option).getByText('Ctrl+K')).toBeInTheDocument();
  });

  it('isSearching 呈现 skeleton 加载行(sr-only 文案在场)', () => {
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    renderWithProviders(
      <PaletteResults {...DEFAULTS} sections={sections} selectedStableId={null} isSearching />,
    );
    // skeleton 的 role=status 在 listbox 内(全局另有 Toast region 亦为 status)
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByRole('status')).toBeInTheDocument();
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
    // React 的 onMouseEnter 由冒泡的 mouseover 合成(jsdom 下以 mouseOver 驱动)
    fireEvent.mouseOver(option);
    expect(onOptionHover).toHaveBeenCalledWith('issue:i-1');
  });

  it('auxClick 中键(button=1)以 newTab:true 上报;左键与其余键不上报', () => {
    const onOptionActivate = vi.fn();
    const sections = buildQuerySections([issueWithHighlight()], [], '登录');
    renderWithProviders(
      <PaletteResults
        {...DEFAULTS}
        sections={sections}
        selectedStableId={null}
        onOptionActivate={onOptionActivate}
      />,
    );
    const option = screen.getByRole('option');
    const auxClick = (button: number): void => {
      fireEvent(option, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button }));
    };
    // 非中键(button=0/2)的 auxclick 不触发新标签语义
    auxClick(0);
    auxClick(2);
    expect(onOptionActivate).not.toHaveBeenCalled();
    auxClick(1);
    expect(onOptionActivate).toHaveBeenCalledTimes(1);
    expect(onOptionActivate).toHaveBeenCalledWith(
      expect.objectContaining({ stableId: 'issue:i-1' }),
      { newTab: true },
    );
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
    const sections: ReadonlyArray<PaletteSection> = buildQuerySections(
      [
        {
          ...item,
          highlight: {
            title: {
              unit: 'codepoint',
              ranges: [
                [0, 2],
                [5, 7],
              ],
            },
          },
        },
      ],
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
