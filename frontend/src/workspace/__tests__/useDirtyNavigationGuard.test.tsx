/**
 * useDirtyNavigationGuard — 脏表单离开确认:
 * - 干净态点击内部链接正常导航;
 * - 脏态点击内部链接被拦截 → 确认 Dialog;stay 留下、discard 前往;
 * - 组合键(新标签)放行;脏态 beforeunload 触发浏览器确认。
 */
import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { DirtyNavigationGuardDialog, useDirtyNavigationGuard } from '../useDirtyNavigationGuard';

// jsdom 未实现锚点导航:守卫有意放行外链/组合键点击(真实浏览器会导航),
// 在冒泡阶段统一 preventDefault 抑制 jsdom 噪音——不影响守卫捕获阶段的判定,
// react-router Link 的导航经 navigate() 完成,亦不受影响。
let removeNavSuppressor: (() => void) | null = null;
beforeEach(() => {
  const suppress = (event: MouseEvent): void => {
    const target = event.target;
    if (target instanceof Element && target.closest('a[href]') !== null) event.preventDefault();
  };
  document.addEventListener('click', suppress);
  removeNavSuppressor = () => document.removeEventListener('click', suppress);
});
afterEach(() => {
  removeNavSuppressor?.();
  removeNavSuppressor = null;
});

function GuardHarness({ dirty }: { dirty: boolean }): React.JSX.Element {
  const guard = useDirtyNavigationGuard(dirty);
  return (
    <div>
      <Link to="/page-b" data-testid="link-b">
        Go B
      </Link>
      <a href="https://external.example/x" data-testid="link-external">
        External
      </a>
      <DirtyNavigationGuardDialog
        isConfirming={guard.isConfirming}
        title="Discard unsaved changes?"
        description="You have unsaved changes."
        stayLabel="Stay"
        discardLabel="Discard"
        closeLabel="Close"
        onStay={guard.stay}
        onDiscard={guard.discard}
      />
    </div>
  );
}

function renderAt(dirty: boolean, route = '/page-a'): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/page-a" element={<GuardHarness dirty={dirty} />} />
      <Route path="/page-b" element={<div data-testid="page-b" />} />
    </Routes>,
    { route },
  );
}

function RequestLeaveHarness(): React.JSX.Element {
  const guard = useDirtyNavigationGuard(true);
  return (
    <div>
      <Link to="/page-b" data-testid="link-b">
        Go B
      </Link>
      <button type="button" data-testid="leave-b" onClick={() => guard.requestLeave('/page-b')}>
        Leave B
      </button>
      <button type="button" data-testid="leave-c" onClick={() => guard.requestLeave('/page-c')}>
        Leave C
      </button>
      <DirtyNavigationGuardDialog
        isConfirming={guard.isConfirming}
        title="Discard unsaved changes?"
        description="You have unsaved changes."
        stayLabel="Stay"
        discardLabel="Discard"
        closeLabel="Close"
        onStay={guard.stay}
        onDiscard={guard.discard}
      />
    </div>
  );
}

function renderRequestLeave(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/page-a" element={<RequestLeaveHarness />} />
      <Route path="/page-b" element={<div data-testid="page-b" />} />
      <Route path="/page-c" element={<div data-testid="page-c" />} />
    </Routes>,
    { route: '/page-a' },
  );
}

describe('useDirtyNavigationGuard', () => {
  it('干净态点击内部链接正常导航(无确认)', async () => {
    const user = userEvent.setup();
    renderAt(false);
    await user.click(screen.getByTestId('link-b'));
    expect(screen.getByTestId('page-b')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('脏态点击内部链接被拦截并弹出确认 Dialog(未导航)', async () => {
    const user = userEvent.setup();
    renderAt(true);
    await user.click(screen.getByTestId('link-b'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByTestId('dirty-guard-stay')).toBeInTheDocument();
    expect(screen.getByTestId('dirty-guard-discard')).toBeInTheDocument();
    // 仍停留在 page-a
    expect(screen.queryByTestId('page-b')).not.toBeInTheDocument();
    expect(screen.getByTestId('link-b')).toBeInTheDocument();
  });

  it('stay → 关闭确认并留在原页', async () => {
    const user = userEvent.setup();
    renderAt(true);
    await user.click(screen.getByTestId('link-b'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.click(screen.getByTestId('dirty-guard-stay'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('page-b')).not.toBeInTheDocument();
  });

  it('discard → 放弃更改并前往被拦截目标', async () => {
    const user = userEvent.setup();
    renderAt(true);
    await user.click(screen.getByTestId('link-b'));
    await user.click(screen.getByTestId('dirty-guard-discard'));
    expect(screen.getByTestId('page-b')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('脏态外部链接不拦截(非应用内路径)', async () => {
    renderAt(true);
    const external = screen.getByTestId('link-external');
    fireEvent.click(external);
    // 外部链接不被本守卫阻止(默认行为交给浏览器),不弹确认
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('脏态组合键点击(新标签)放行不弹确认', () => {
    renderAt(true);
    fireEvent.click(screen.getByTestId('link-b'), { ctrlKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('脏态 beforeunload 触发浏览器确认(preventDefault)', () => {
    renderAt(true);
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('干净态 beforeunload 不拦截', () => {
    renderAt(false);
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it('requestLeave → 弹出确认;discard 前往请求的路径', async () => {
    const user = userEvent.setup();
    renderRequestLeave();
    await user.click(screen.getByTestId('leave-c'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByTestId('page-c')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('dirty-guard-discard'));
    expect(screen.getByTestId('page-c')).toBeInTheDocument();
  });

  it('requestLeave → stay 留在原页并关闭确认', async () => {
    const user = userEvent.setup();
    renderRequestLeave();
    await user.click(screen.getByTestId('leave-b'));
    await user.click(screen.getByTestId('dirty-guard-stay'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('page-b')).not.toBeInTheDocument();
  });

  it('已有待确认目标时 requestLeave 不覆盖(先到的导航请求优先)', async () => {
    const user = userEvent.setup();
    renderRequestLeave();
    await user.click(screen.getByTestId('leave-b'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // 第二次请求不覆盖第一次的目标
    await user.click(screen.getByTestId('leave-c'));
    await user.click(screen.getByTestId('dirty-guard-discard'));
    expect(screen.getByTestId('page-b')).toBeInTheDocument();
    expect(screen.queryByTestId('page-c')).not.toBeInTheDocument();
  });
});
