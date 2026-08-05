/**
 * 看板列组件交互测试(design-quality §9.4/§10.2/§11.4):
 * 指针拖拽(阈值/浮层/目标列高亮/落点指示线/中点定位/Esc 取消/禁用门控)、
 * WIP 预检(warn 放行 / block 禁落)、键盘移动模式、快速创建打磨、虚拟化开关、折叠。
 *
 * 拖拽经指针事件模拟(jsdom 无 PointerEvent/布局,见 dragTestUtils 说明)。
 */
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { act, fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ShortcutProvider } from '../../../shortcuts/ShortcutProvider';
import { useSettingsStore } from '../../../state/settingsStore';
import { renderWithProviders } from '../../../test-utils/render';
import { useShortcutRegistry } from '../../../shortcuts';
import { BoardColumns, computeDropPosition } from '../BoardColumns';
import type { BoardCard } from '../projection';
import type { BoardColumn } from '../types';
import { ensurePointerEvent, mockRect } from './dragTestUtils';

const originalPreferences = useSettingsStore.getState().preferences;

function card(id: string, position: number): BoardCard {
  return {
    id,
    identifier: `WEB-${id}`,
    title: `Card ${id}`,
    state_category: 'todo',
    status: { id: 'st', name: 'Todo', category: 'todo' },
    status_id: 'st',
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position,
    version: 1,
    updated_at: '',
  };
}

function column(overrides: Partial<BoardColumn> = {}): BoardColumn {
  return {
    key: 'todo',
    label: 'board.category.todo',
    collapsed: false,
    wip: null,
    count: 0,
    placeholder: false,
    ...overrides,
  };
}

type RenderProps = Partial<Parameters<typeof BoardColumns>[0]>;

function render(props: RenderProps = {}) {
  const onDropCard = vi.fn();
  const onQuickCreate = vi.fn();
  const onToggleCollapse = vi.fn();
  const view = renderWithProviders(
    <BoardColumns
      columns={[column()]}
      groupBy="state_category"
      cardsByKey={{}}
      canWrite
      dragEnabled
      onToggleCollapse={onToggleCollapse}
      onDropCard={onDropCard}
      onQuickCreate={onQuickCreate}
      {...props}
    />,
  );
  return { onDropCard, onQuickCreate, onToggleCollapse, ...view };
}

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <output data-testid="location-probe">{`${location.pathname}${location.search}`}</output>;
}

/** Production-like parent owner: BoardPage owns keyboard handlers and board context. */
function BoardOpenShortcutOwner(): null {
  const navigate = useNavigate();
  useEffect(() => {
    const registry = useShortcutRegistry.getState();
    registry.setContexts(['global', 'board']);
    const unregister = registry.registerShortcuts([
      {
        id: 'board.open.card',
        combo: 'enter',
        label: 'Open card',
        group: 'board',
        run: () => navigate('/issues/a'),
      },
    ]);
    return () => {
      unregister();
      registry.setContexts([]);
    };
  }, [navigate]);
  return null;
}

/** 两列(todo 源 / done 目标)+ 矩形 mock,返回拖拽起点卡。 */
function setupDragScene() {
  const onDropCard = vi.fn();
  renderWithProviders(
    <BoardColumns
      columns={[column({ key: 'todo' }), column({ key: 'done', label: 'board.category.done' })]}
      groupBy="state_category"
      cardsByKey={{ todo: [card('a', 2), card('b', 4)], done: [card('c', 10), card('d', 20)] }}
      canWrite
      dragEnabled
      onToggleCollapse={vi.fn()}
      onDropCard={onDropCard}
      onQuickCreate={vi.fn()}
    />,
  );
  mockRect(screen.getByTestId('board-card-a'), { left: 0, top: 0, right: 100, bottom: 40 });
  mockRect(screen.getByTestId('board-column-todo'), { left: 0, top: 0, right: 100, bottom: 600 });
  mockRect(screen.getByTestId('board-column-done'), { left: 200, top: 0, right: 300, bottom: 600 });
  mockRect(screen.getByTestId('board-card-c'), { top: 100, bottom: 140 });
  mockRect(screen.getByTestId('board-card-d'), { top: 160, bottom: 200 });
  return { onDropCard, cardA: screen.getByTestId('board-card-a') };
}

describe('BoardColumns 渲染', () => {
  beforeEach(() => {
    ensurePointerEvent();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => useSettingsStore.setState({ preferences: originalPreferences }));
  });

  it('落点计算在投影数组暂态稀疏时仍使用安全位置回退', () => {
    const sparse = Array<BoardCard>(2);
    expect(computeDropPosition(sparse, null)).toBe(1);
    expect(computeDropPosition(sparse, 0)).toBe(-1);
    expect(computeDropPosition(sparse, 1)).toBe(0);
  });

  it('渲染列内卡片;无卡片呈现空态文案', () => {
    render({ cardsByKey: { todo: [card('a', 1), card('b', 2)] } });
    const todo = screen.getByTestId('board-column-todo');
    expect(screen.getByTestId('board-card-a')).toBeInTheDocument();
    expect(screen.getByTestId('board-card-b')).toBeInTheDocument();
    const heading = screen.getByRole('heading', { name: 'Todo', level: 2 });
    expect(todo).toHaveAttribute('aria-labelledby', heading.id);
    expect(todo).not.toHaveAttribute('aria-label');
  });

  it('卡片可聚焦(键盘移动入口,§10.2)且标注 aria-keyshortcuts', () => {
    render({ cardsByKey: { todo: [card('a', 1)] } });
    const cardA = screen.getByTestId('board-card-a');
    expect(cardA).toHaveAttribute('tabindex', '0');
    expect(cardA).toHaveAttribute('aria-keyshortcuts');
    // 可访问名由真实卡片内容构成；不把未经本地化的英文 role 描述泄漏给读屏。
    expect(cardA).not.toHaveAttribute('aria-roledescription');
  });

  it('仅注册 palette 命令，保留 BoardPage 所有的上下文与快捷键', () => {
    useShortcutRegistry.getState().setContexts(['global', 'board']);
    const view = render({ cardsByKey: { todo: [card('a', 1)] } });
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'board']);
    expect(
      useShortcutRegistry
        .getState()
        .commands.filter((entry) => entry.group === 'board')
        .map((entry) => entry.id)
        .sort(),
    ).toEqual(
      [
        'board.move.up.vim',
        'board.move.down.vim',
        'board.move.left.vim',
        'board.move.right.vim',
        'board.new.card',
        'board.change.status',
        'board.change.assignee',
        'board.open.card',
        'board.filter',
      ].sort(),
    );
    expect(useShortcutRegistry.getState().shortcuts).toEqual([]);

    view.unmount();
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'board']);
    expect(useShortcutRegistry.getState().commands).toEqual([]);
  });

  it('palette 命令委托给 BoardPage 注册的同 id handler', () => {
    const ids = [
      'board.move.up.vim',
      'board.move.down.vim',
      'board.move.left.vim',
      'board.move.right.vim',
      'board.new.card',
      'board.change.status',
      'board.change.assignee',
      'board.open.card',
      'board.filter',
    ] as const;
    const runs = ids.map(() => vi.fn());
    useShortcutRegistry.getState().registerShortcuts(
      ids.map((id, index) => ({
        id,
        combo: `test-${index}`,
        label: id,
        group: 'board' as const,
        run: runs[index]!,
      })),
    );
    render({ cardsByKey: { todo: [card('a', 1)] } });

    for (const [index, id] of ids.entries()) {
      const command = useShortcutRegistry.getState().commands.find((entry) => entry.id === id);
      expect(command).toBeDefined();
      act(() => command?.run());
      expect(runs[index]).toHaveBeenCalledTimes(1);
    }
  });

  it('聚焦卡片建立本地可见选中态，数据删除选中卡后回到首卡', () => {
    const columns = [
      column({ key: 'todo' }),
      column({ key: 'empty', label: 'Empty' }),
      column({ key: 'done', label: 'board.category.done' }),
    ];
    const baseProps = {
      columns,
      groupBy: 'state_category',
      canWrite: true,
      dragEnabled: true,
      onToggleCollapse: vi.fn(),
      onDropCard: vi.fn(),
      onQuickCreate: vi.fn(),
    } as const;
    function RefreshHarness(): React.JSX.Element {
      const [cardsByKey, setCardsByKey] = useState({
        todo: [card('a', 1), card('b', 2)],
        empty: [] as BoardCard[],
        done: [card('c', 1)],
      });
      return (
        <>
          <button
            type="button"
            data-testid="remove-selected-card"
            onClick={() => setCardsByKey({ todo: [card('a', 1)], empty: [], done: [] })}
          />
          <BoardColumns {...baseProps} cardsByKey={cardsByKey} />
        </>
      );
    }
    renderWithProviders(<RefreshHarness />);
    fireEvent.focus(screen.getByTestId('board-card-b'));
    expect(screen.getByTestId('board-card-b')).toHaveAttribute('aria-current', 'true');

    fireEvent.click(screen.getByTestId('remove-selected-card'));
    expect(screen.getByTestId('board-card-a')).toHaveAttribute('aria-current', 'true');
  });

  it('父页面尚未注册 handler 时 palette 命令是安全 no-op', () => {
    render({ columns: [], cardsByKey: {} });
    const commands = useShortcutRegistry
      .getState()
      .commands.filter((entry) => entry.group === 'board');
    expect(commands).toHaveLength(9);
    for (const command of commands) {
      expect(() => act(() => command.run())).not.toThrow();
    }
  });

  it('卡片 focus 与 pointerdown 均同步本地选中态', () => {
    render({ cardsByKey: { todo: [card('a', 1)] } });
    const cardA = screen.getByTestId('board-card-a');
    fireEvent.focus(cardA);
    expect(cardA).toHaveAttribute('aria-current', 'true');
    fireEvent.pointerDown(cardA, { pointerId: 1, pointerType: 'mouse', button: 0 });
    expect(cardA).toHaveAttribute('aria-current', 'true');
  });

  it('紧凑视口通过单泳道渲染列体', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        matches: true,
        media: '(max-width: 599px)',
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    render({ cardsByKey: { todo: [card('a', 1)] } });
    expect(screen.getByTestId('board-compact')).toBeInTheDocument();
    expect(screen.getByTestId('compact-body')).toContainElement(screen.getByTestId('board-card-a'));
  });

  it('折叠列不渲染列体;展开/折叠回调', () => {
    const { onToggleCollapse } = render({ columns: [column({ collapsed: true })] });
    expect(screen.queryByTestId('column-body-todo')).not.toBeInTheDocument();
    const toggle = screen.getByRole('button', { expanded: false });
    expect(toggle).toHaveAttribute('data-slot', 'button');
    fireEvent.click(toggle);
    expect(onToggleCollapse).toHaveBeenCalledWith('todo');
  });

  it('WIP 超限徽章附 warning 图标(非仅颜色,§13.2)', () => {
    render({ columns: [column({ wip: { limit: 1, enforcement: 'block' }, count: 2 })] });
    const badge = screen.getByTestId('wip-badge-todo');
    expect(badge).toHaveTextContent('2/1');
    expect(badge.querySelector('svg')).not.toBeNull();
  });

  it('WIP warn 超限徽章使用 warn 色调', () => {
    render({ columns: [column({ wip: { limit: 1, enforcement: 'warn' }, count: 2 })] });
    expect(screen.getByTestId('wip-badge-todo').className).toContain('mesh-board__wip--warn');
  });

  it('priority 分组列走 i18n 标签', () => {
    render({
      groupBy: 'priority',
      columns: [column({ key: 'high', label: 'board.priority.high' })],
    });
    expect(screen.getByTestId('board-column-high')).toHaveTextContent('High');
  });

  it('固定分组列暴露语义色调类，动态分组保持中性', () => {
    const view = render({
      columns: [
        column({ key: 'backlog', label: 'board.category.backlog' }),
        column({ key: 'in_progress', label: 'board.category.in_progress' }),
        column({ key: 'in_review', label: 'board.category.in_review' }),
      ],
      groupBy: 'state_category',
    });
    expect(screen.getByTestId('board-column-backlog')).toHaveClass('mesh-board__column--backlog');
    expect(screen.getByTestId('board-column-in_progress')).toHaveClass(
      'mesh-board__column--in_progress',
    );
    expect(screen.getByTestId('board-column-in_review')).toHaveClass(
      'mesh-board__column--in_review',
    );

    view.unmount();
    render({ groupBy: 'status', columns: [column({ key: 'status-1', label: 'Status A' })] });
    expect(screen.getByTestId('board-column-status-1')).toHaveClass('mesh-board__column--neutral');
  });

  it('卡片有负责人时渲染负责人名', () => {
    const withAssignee: BoardCard = { ...card('a', 1), assignee: { id: 'u1', name: '张三' } };
    render({ cardsByKey: { todo: [withAssignee] } });
    expect(screen.getByTestId('board-card-a')).toHaveTextContent('张三');
  });

  it('按视图 card_fields 呈现信息密集元数据并可逐项隐藏', () => {
    const richCard: BoardCard = {
      ...card('a', 1),
      description: 'Two-line supporting detail',
      project: { id: 'p1', name: 'Web project', key: 'WEB' },
      estimate: 5,
      estimate_unit: 'points',
      due_date: '2026-08-10',
      assignee: { id: 'u1', name: 'Jane' },
      updated_at: '2026-08-05T03:04:05Z',
    };
    const view = render({ cardsByKey: { todo: [richCard] } });
    const rendered = screen.getByTestId('board-card-a');
    expect(rendered).toHaveTextContent('Two-line supporting detail');
    expect(rendered).toHaveTextContent('Web project');
    expect(rendered).toHaveTextContent('5 Points');
    expect(rendered).toHaveTextContent('8/10/26');
    expect(rendered).toHaveTextContent('Jane');
    expect(rendered.querySelector('time')).toHaveAttribute('datetime', '2026-08-05T03:04:05Z');

    view.unmount();
    render({ cardsByKey: { todo: [richCard] }, cardFields: [] });
    const compact = screen.getByTestId('board-card-a');
    expect(compact).not.toHaveTextContent('Two-line supporting detail');
    expect(compact).not.toHaveTextContent('Web project');
    expect(compact).not.toHaveTextContent('5 Points');
    expect(compact).not.toHaveTextContent('8/10/26');
    expect(compact).not.toHaveTextContent('Jane');
    expect(compact.querySelector('time')).toBeNull();
  });

  it('按用户 locale 与 timezone 本地化卡片优先级、估算与日期', () => {
    act(() => {
      useSettingsStore.setState({
        preferences: { theme: null, locale: 'zh-CN', timezone: 'Asia/Shanghai' },
      });
    });
    const localizedCard: BoardCard = {
      ...card('a', 1),
      estimate: 1234.5,
      estimate_unit: 'points',
      due_date: '2026-08-10',
      updated_at: '2026-08-05T17:04:05Z',
    };

    render({ cardsByKey: { todo: [localizedCard] } });

    const rendered = screen.getByTestId('board-card-a');
    expect(rendered).toHaveTextContent('高');
    expect(rendered).toHaveTextContent('1,234.5 点数');
    expect(rendered).toHaveTextContent('2026/8/10');
    expect(rendered.querySelector('time')).toHaveTextContent('2026/8/6');
  });

  it('groupBy=null 的 __dynamic__ 列回退空分组名(不崩溃)', () => {
    render({ groupBy: null, columns: [column({ key: '__dynamic__', label: 'x' })] });
    expect(screen.getByTestId('board-column-__dynamic__')).toBeInTheDocument();
  });

  it('空列集合渲染为空看板(不崩溃)', () => {
    render({ columns: [] });
    expect(screen.getByTestId('board-columns')).toBeInTheDocument();
  });

  it('动态分组列直用服务端 label;__dynamic__ 占位列走 i18n 说明', () => {
    render({ groupBy: 'status', columns: [column({ key: 'st_a', label: 'Status A' })] });
    expect(screen.getByTestId('board-column-st_a')).toHaveTextContent('Status A');
    render({ groupBy: 'status', columns: [column({ key: '__dynamic__', label: 'x' })] });
    // __dynamic__ 占位列渲染投影增量说明(插值 groupBy=status)。
    expect(screen.getAllByTestId('board-column-__dynamic__').at(-1)).toHaveTextContent(
      'Columns for status',
    );
  });
});

describe('BoardColumns 指针拖拽(§9.4)', () => {
  beforeEach(() => {
    ensurePointerEvent();
    vi.useFakeTimers(); // 回位动画定时器(§9.4.4)经 fake timers 精确推进
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('阈值进入拖拽 → 浮层出现 → 目标列高亮 + 指示线 → 落点中点定位', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 20, clientY: 10 }); // 越阈值 → 进入拖拽
    expect(screen.getByTestId('board-drag-clone')).toBeInTheDocument();
    expect(screen.getByTestId('board-live').textContent).toContain('Started dragging WEB-a');

    fireEvent.pointerMove(document, { clientX: 250, clientY: 150 }); // 命中 done,index 1
    expect(screen.getByTestId('board-drop-indicator')).toBeInTheDocument();
    expect(screen.getByTestId('board-column-done').className).toContain(
      'mesh-board__column--drag-over',
    );

    fireEvent.pointerUp(document, { clientX: 250, clientY: 150 });
    // 中点定位:(10+20)/2 = 15。
    expect(onDropCard).toHaveBeenCalledWith('a', 'done', 15);
    // §9.4.4 回位动画:浮层先进入 returning(滑回源卡),动画结束后清除。
    expect(screen.getByTestId('board-drag-clone').className).toContain(
      'mesh-board-drag__clone--returning',
    );
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
  });

  it('未越阈值不进入拖拽', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 12, clientY: 10 }); // 2px < 6
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
    fireEvent.pointerUp(document, { clientX: 12, clientY: 10 });
    expect(onDropCard).not.toHaveBeenCalled();
  });

  it('Esc 取消拖拽:浮层消失 + 播报取消 + 不落点', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 20, clientY: 10 });
    expect(screen.getByTestId('board-drag-clone')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    // 取消也经回位动画后清除(§9.4.4)。
    expect(screen.getByTestId('board-drag-clone').className).toContain(
      'mesh-board-drag__clone--returning',
    );
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
    expect(onDropCard).not.toHaveBeenCalled();
    expect(screen.getByTestId('board-live').textContent).toContain('Cancelled dragging WEB-a');
  });

  it('dragEnabled=false 时拖拽不生效', () => {
    render({ dragEnabled: false, cardsByKey: { todo: [card('a', 1)] } });
    const cardA = screen.getByTestId('board-card-a');
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 300, clientY: 300 });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
  });

  it('右键(非主键)鼠标按下不触发拖拽', () => {
    setupDragScene();
    const cardA = screen.getByTestId('board-card-a');
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 2, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 300, clientY: 300 });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
  });

  it('触摸端移动越过阈值会交还滚动手势，不启动精细指针拖拽或长按 sheet', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'touch' });
    fireEvent.pointerMove(document, { clientX: 10, clientY: 40 });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(screen.queryByTestId('board-touch-sheet')).not.toBeInTheDocument();
    fireEvent.pointerUp(document, { clientX: 250, clientY: 150 });
    expect(onDropCard).not.toHaveBeenCalled();
  });

  it('pointercancel 取消活动拖拽并回位，不提交落点', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    // 非 Escape 键不应干预追踪状态。
    fireEvent.keyDown(document, { key: 'Shift' });
    fireEvent.pointerMove(document, { clientX: 20, clientY: 10 });
    fireEvent.pointerMove(document, { clientX: 250, clientY: 150 });
    expect(screen.getByTestId('board-drag-clone')).toBeInTheDocument();

    fireEvent.pointerCancel(document, { clientX: 250, clientY: 150 });

    expect(onDropCard).not.toHaveBeenCalled();
    expect(screen.getByTestId('board-live')).toHaveTextContent('Cancelled dragging WEB-a');
    expect(screen.getByTestId('board-drag-clone')).toHaveClass('mesh-board-drag__clone--returning');
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
  });

  it('pointercancel 可在触摸长按尚未触发时清理计时器', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, {
      clientX: 10,
      clientY: 10,
      button: 0,
      pointerType: 'touch',
    });
    fireEvent.pointerCancel(document, { clientX: 10, clientY: 10 });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(screen.queryByTestId('board-touch-sheet')).not.toBeInTheDocument();
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
    expect(onDropCard).not.toHaveBeenCalled();
  });

  it('触摸长按打开等价移动 sheet，选列后落库路径并关闭', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, {
      clientX: 10,
      clientY: 10,
      button: 0,
      pointerType: 'touch',
    });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(screen.getByTestId('board-touch-sheet')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('touch-column-done'));
    expect(onDropCard).toHaveBeenCalledWith('a', 'done', 21);
    expect(screen.queryByTestId('board-touch-sheet')).not.toBeInTheDocument();
    fireEvent.pointerUp(document, { clientX: 10, clientY: 10 });
  });

  it('拖到列间隙(未命中任何列)抬起 → 不落点', () => {
    const { onDropCard, cardA } = setupDragScene();
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 20, clientY: 10 }); // 进入拖拽
    fireEvent.pointerMove(document, { clientX: 150, clientY: 300 }); // 两列间隙,未命中
    expect(screen.queryByTestId('board-drop-indicator')).not.toBeInTheDocument();
    fireEvent.pointerUp(document, { clientX: 150, clientY: 300 });
    expect(onDropCard).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.queryByTestId('board-drag-clone')).not.toBeInTheDocument();
  });

  it('WIP block 满载列:危险条 + 无指示线 + 禁落', () => {
    const onDropCard = vi.fn();
    renderWithProviders(
      <BoardColumns
        columns={[
          column({ key: 'todo' }),
          column({
            key: 'in_progress',
            label: 'board.category.in_progress',
            wip: { limit: 1, enforcement: 'block' },
            count: 1,
          }),
        ]}
        groupBy="state_category"
        cardsByKey={{ todo: [card('a', 2)], in_progress: [card('x', 5)] }}
        canWrite
        dragEnabled
        onToggleCollapse={vi.fn()}
        onDropCard={onDropCard}
        onQuickCreate={vi.fn()}
      />,
    );
    mockRect(screen.getByTestId('board-card-a'), { left: 0, top: 0, right: 100, bottom: 40 });
    mockRect(screen.getByTestId('board-column-todo'), { left: 0, top: 0, right: 100, bottom: 600 });
    mockRect(screen.getByTestId('board-column-in_progress'), {
      left: 200,
      top: 0,
      right: 300,
      bottom: 600,
    });
    const cardA = screen.getByTestId('board-card-a');
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 20, clientY: 10 });
    fireEvent.pointerMove(document, { clientX: 250, clientY: 300 });
    const strip = screen.getByTestId('board-wip-strip-in_progress');
    expect(strip.className).toContain('block');
    expect(strip.textContent).toContain('This column has reached its WIP limit; drop is blocked');
    expect(screen.queryByTestId('board-drop-indicator')).not.toBeInTheDocument();
    fireEvent.pointerUp(document, { clientX: 250, clientY: 300 });
    expect(onDropCard).not.toHaveBeenCalled();
  });

  it('WIP warn 超限列:警告条但允许落点', () => {
    const onDropCard = vi.fn();
    renderWithProviders(
      <BoardColumns
        columns={[
          column({ key: 'todo' }),
          column({
            key: 'in_progress',
            label: 'board.category.in_progress',
            wip: { limit: 1, enforcement: 'warn' },
            count: 1,
          }),
        ]}
        groupBy="state_category"
        cardsByKey={{ todo: [card('a', 2)], in_progress: [card('x', 5)] }}
        canWrite
        dragEnabled
        onToggleCollapse={vi.fn()}
        onDropCard={onDropCard}
        onQuickCreate={vi.fn()}
      />,
    );
    mockRect(screen.getByTestId('board-card-a'), { left: 0, top: 0, right: 100, bottom: 40 });
    mockRect(screen.getByTestId('board-column-todo'), { left: 0, top: 0, right: 100, bottom: 600 });
    mockRect(screen.getByTestId('board-column-in_progress'), {
      left: 200,
      top: 0,
      right: 300,
      bottom: 600,
    });
    const cardA = screen.getByTestId('board-card-a');
    fireEvent.pointerDown(cardA, { clientX: 10, clientY: 10, button: 0, pointerType: 'mouse' });
    fireEvent.pointerMove(document, { clientX: 20, clientY: 10 });
    fireEvent.pointerMove(document, { clientX: 250, clientY: 300 });
    const strip = screen.getByTestId('board-wip-strip-in_progress');
    expect(strip.className).toContain('warn');
    expect(strip.textContent).toContain(
      'This column will exceed its WIP limit (warning, drop allowed)',
    );
    expect(screen.getByTestId('board-drop-indicator')).toBeInTheDocument();
    fireEvent.pointerUp(document, { clientX: 250, clientY: 300 });
    // 空位(卡 x 之下)→ 末张+1 = 6。
    expect(onDropCard).toHaveBeenCalledWith('a', 'in_progress', 6);
  });
});

describe('BoardColumns 键盘移动模式(§9.4.5/§10.2)', () => {
  beforeEach(() => ensurePointerEvent());
  afterEach(() => vi.unstubAllGlobals());

  function setupMove() {
    const onDropCard = vi.fn();
    renderWithProviders(
      <BoardColumns
        columns={[
          column({ key: 'todo' }),
          column({ key: 'in_progress', label: 'board.category.in_progress' }),
        ]}
        groupBy="state_category"
        cardsByKey={{ todo: [card('a', 2)], in_progress: [] }}
        canWrite
        dragEnabled
        onToggleCollapse={vi.fn()}
        onDropCard={onDropCard}
        onQuickCreate={vi.fn()}
      />,
    );
    return { onDropCard, cardA: screen.getByTestId('board-card-a') };
  }

  it('方向键进入移动模式 → 选列 → Enter 确认落点', () => {
    const { onDropCard, cardA } = setupMove();
    fireEvent.keyDown(cardA, { key: 'ArrowRight' });
    expect(screen.getByTestId('board-live').textContent).toContain('Move mode entered for WEB-a.');
    expect(cardA.className).toContain('mesh-board__card--selected');
    // 再按右键 → 目标列切到 in_progress。
    fireEvent.keyDown(cardA, { key: 'ArrowRight' });
    expect(screen.getByTestId('board-column-in_progress').className).toContain(
      'mesh-board__column--move-target',
    );
    fireEvent.keyDown(cardA, { key: 'Enter' });
    // 空列 → computeDropPosition([], null) = 1。
    expect(onDropCard).toHaveBeenCalledWith('a', 'in_progress', 1);
    expect(screen.getByTestId('board-live').textContent).toContain('Moved WEB-a to In Progress');
  });

  it('移动模式已消费的 Enter 不再冒泡到 window 全局快捷键层', () => {
    const { onDropCard, cardA } = setupMove();
    const bubbled = vi.fn();
    window.addEventListener('keydown', bubbled);
    try {
      fireEvent.keyDown(cardA, { key: 'ArrowRight' });
      fireEvent.keyDown(cardA, { key: 'ArrowRight' });
      bubbled.mockClear();
      fireEvent.keyDown(cardA, { key: 'Enter' });
      expect(onDropCard).toHaveBeenCalledTimes(1);
      expect(bubbled).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('keydown', bubbled);
    }
  });

  it('上下键调整列内位置', () => {
    const { onDropCard, cardA } = setupMove();
    fireEvent.keyDown(cardA, { key: 'ArrowDown' }); // 进入
    fireEvent.keyDown(cardA, { key: 'ArrowDown' }); // 位置下移
    fireEvent.keyDown(cardA, { key: 'Enter' });
    expect(onDropCard).toHaveBeenCalled();
  });

  it('Esc 取消移动模式', () => {
    const { onDropCard, cardA } = setupMove();
    fireEvent.keyDown(cardA, { key: 'ArrowRight' });
    fireEvent.keyDown(cardA, { key: 'Escape' });
    expect(screen.getByTestId('board-live').textContent).toContain('Move mode cancelled');
    fireEvent.keyDown(cardA, { key: 'Enter' });
    expect(onDropCard).not.toHaveBeenCalled();
  });
});

describe('BoardColumns ↔ 快捷键分发仲裁(§4.3.1 一键一 handler:移动模式 > 卡片打开)', () => {
  beforeEach(() => {
    ensurePointerEvent();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
  });

  /** 真实分发链路:卡片 React onKeyDown + window 级 ShortcutProvider 同栈共存。 */
  function setupArbitration() {
    const onDropCard = vi.fn();
    renderWithProviders(
      <ShortcutProvider isMac={false}>
        <BoardOpenShortcutOwner />
        <LocationProbe />
        <BoardColumns
          columns={[
            column({ key: 'todo' }),
            column({ key: 'in_progress', label: 'board.category.in_progress' }),
          ]}
          groupBy="state_category"
          cardsByKey={{ todo: [card('a', 2)], in_progress: [] }}
          canWrite
          dragEnabled
          onToggleCollapse={vi.fn()}
          onDropCard={onDropCard}
          onQuickCreate={vi.fn()}
        />
      </ShortcutProvider>,
      { route: '/board' },
    );
    return {
      onDropCard,
      cardA: screen.getByTestId('board-card-a'),
      location: screen.getByTestId('location-probe'),
    };
  }

  it('移动模式中 Enter 只确认移动,不触发 board.open.card(一次按键一个 handler)', () => {
    const { onDropCard, cardA, location } = setupArbitration();
    fireEvent.keyDown(cardA, { key: 'ArrowRight' }); // 进入移动模式
    fireEvent.keyDown(cardA, { key: 'ArrowRight' }); // 选目标列 in_progress
    fireEvent.keyDown(cardA, { key: 'Enter' }); // 确认移动
    expect(onDropCard).toHaveBeenCalledWith('a', 'in_progress', 1);
    expect(screen.getByTestId('board-live').textContent).toContain('Moved WEB-a to In Progress');
    expect(location).toHaveTextContent('/board');
  });

  it('移动模式中方向键不穿透为选中移动(排他消费,选中态不散失)', () => {
    const { cardA, location } = setupArbitration();
    fireEvent.keyDown(cardA, { key: 'ArrowDown' }); // 进入移动模式
    fireEvent.keyDown(cardA, { key: 'ArrowRight' }); // 选目标列
    // 最近播报为目标列(移动模式内);卡片保持选中;window 分发器未另触发打开。
    expect(screen.getByTestId('board-live').textContent).toContain('Target column In Progress');
    expect(cardA.className).toContain('mesh-board__card--selected');
    expect(location).toHaveTextContent('/board');
  });

  it('非移动模式 Enter 打开卡片:board.open.card 正常触发(既有行为不回归)', () => {
    const { onDropCard, cardA, location } = setupArbitration();
    fireEvent.keyDown(cardA, { key: 'Enter' });
    expect(location).toHaveTextContent('/issues/a');
    expect(onDropCard).not.toHaveBeenCalled();
  });
});

describe('BoardColumns 快速创建打磨(§4.5)', () => {
  beforeEach(() => ensurePointerEvent());
  afterEach(() => vi.unstubAllGlobals());

  it('回车提交并清空;空标题不提交', async () => {
    const { onQuickCreate } = render();
    const input = screen.getByTestId('quick-add-todo');
    expect(input).toHaveAttribute('data-slot', 'input');
    fireEvent.change(input, { target: { value: '新卡' } });
    // act 包裹以冲刷 submit 内 pending 微任务(setPending)。
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    expect(onQuickCreate).toHaveBeenCalledWith('todo', '新卡');
    expect(input).toHaveValue('');
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    expect(onQuickCreate).toHaveBeenCalledTimes(1);
  });

  it('Esc 清空输入', () => {
    render();
    const input = screen.getByTestId('quick-add-todo');
    fireEvent.change(input, { target: { value: '待清除' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(input).toHaveValue('');
  });

  it('无权限时禁用', () => {
    render({ canWrite: false });
    expect(screen.getByTestId('quick-add-todo')).toBeDisabled();
  });

  it('提交期间呈现内联 pending,完成后消失', async () => {
    let resolveCreate: () => void = () => undefined;
    const onQuickCreate = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveCreate = resolve;
        }),
    );
    render({ onQuickCreate });
    const input = screen.getByTestId('quick-add-todo');
    fireEvent.change(input, { target: { value: 'x' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByTestId('quick-add-pending-todo')).toBeInTheDocument();
    await act(async () => {
      resolveCreate();
    });
    expect(screen.queryByTestId('quick-add-pending-todo')).not.toBeInTheDocument();
  });
});

describe('BoardColumns 虚拟化开关(§11.4)', () => {
  beforeEach(() => ensurePointerEvent());
  afterEach(() => vi.unstubAllGlobals());

  it('≥200 卡片启用虚拟化(仅渲染窗口)', () => {
    const many = Array.from({ length: 250 }, (_, i) => card(`c${i}`, i + 1));
    render({ cardsByKey: { todo: many } });
    expect(screen.getByTestId('virtual-column-body')).toBeInTheDocument();
    expect(screen.getByTestId('board-card-c0')).toBeInTheDocument();
    expect(screen.queryByTestId('board-card-c249')).not.toBeInTheDocument();
  });

  it('可切换为完整读屏列表并连续挂载全部卡片，再切回高性能窗口', () => {
    const many = Array.from({ length: 250 }, (_, i) => card(`c${i}`, i + 1));
    render({ cardsByKey: { todo: many } });

    fireEvent.click(screen.getByRole('button', { name: 'Use complete screen reader list' }));
    expect(screen.queryByTestId('virtual-column-body')).not.toBeInTheDocument();
    expect(screen.getByTestId('board-card-c249')).toBeInTheDocument();
    expect(screen.getByTestId('board-columns-wrap')).toHaveAttribute('data-a11y-list-mode', 'full');

    fireEvent.click(screen.getByRole('button', { name: 'Use high-performance virtual list' }));
    expect(screen.getByTestId('virtual-column-body')).toBeInTheDocument();
    expect(screen.queryByTestId('board-card-c249')).not.toBeInTheDocument();
  });

  it('<200 卡片不虚拟化(全部渲染)', () => {
    const few = Array.from({ length: 5 }, (_, i) => card(`c${i}`, i + 1));
    render({ cardsByKey: { todo: few } });
    expect(screen.queryByTestId('virtual-column-body')).not.toBeInTheDocument();
    expect(screen.getByTestId('board-card-c4')).toBeInTheDocument();
  });

  it('虚拟化列进入键盘移动模式:选中卡保持渲染并高亮', () => {
    const many = Array.from({ length: 250 }, (_, i) => card(`c${i}`, i + 1));
    render({ cardsByKey: { todo: many } });
    const c0 = screen.getByTestId('board-card-c0');
    fireEvent.keyDown(c0, { key: 'ArrowDown' }); // 进入移动模式
    expect(screen.getByTestId('board-card-c0').className).toContain('mesh-board__card--selected');
  });
});
