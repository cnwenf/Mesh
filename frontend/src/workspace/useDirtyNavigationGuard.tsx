/**
 * useDirtyNavigationGuard — 脏表单离开确认(design-quality.md §3.2 dirty state / §7.4 保存失败不丢值)。
 *
 * 应用使用声明式路由(非 data router),react-router 的 useBlocker 不可用,故以:
 * - `beforeunload`:拦截刷新/关页(浏览器原生确认);
 * - 文档级 click 捕获:拦截应用内 `<a href="/…">` 导航,弹出确认 Dialog(stay/discard);
 *   discard 经 useNavigate 继续前往被拦截目标,stay 留在原页(值不丢)。
 * 仅在 `dirty` 为真时挂监听;修改键/新标签等组合键放行。
 */
import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { Button, Dialog } from '../design';

export interface DirtyNavigationGuardResult {
  /** 是否正在显示离开确认 */
  isConfirming: boolean;
  /** 留在当前页(关闭确认,保留更改) */
  stay: () => void;
  /** 放弃更改并前往被拦截目标 */
  discard: () => void;
}

/** 是否为应用内路径(以单个 / 开头,排除协议相对 //)。 */
function isInternalPath(href: string): boolean {
  return href.startsWith('/') && !href.startsWith('//');
}

/** 从 href 取出路径部分(去掉 query/hash)用于与当前路径比较。 */
function pathOnly(href: string): string {
  return href.split('?')[0].split('#')[0];
}

export function useDirtyNavigationGuard(dirty: boolean): DirtyNavigationGuardResult {
  const navigate = useNavigate();
  const location = useLocation();
  const [pendingPath, setPendingPath] = useState<string | null>(null);

  // 刷新/关页拦截(浏览器原生确认)。
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // 应用内导航拦截:脏态下点击内部链接 → 阻止默认并记录目标。
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: MouseEvent): void => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest('a[href]');
      if (anchor === null) return;
      const href = anchor.getAttribute('href');
      if (href === null || !isInternalPath(href)) return;
      if (pathOnly(href) === location.pathname) return;
      event.preventDefault();
      setPendingPath(href);
    };
    document.addEventListener('click', handler, true);
    return () => document.removeEventListener('click', handler, true);
  }, [dirty, location.pathname]);

  const stay = useCallback(() => setPendingPath(null), []);
  const discard = useCallback(() => {
    // 在事件处理器中导航(勿置于 setState 更新器内,避免渲染期更新其他组件)。
    if (pendingPath !== null) navigate(pendingPath);
    setPendingPath(null);
  }, [navigate, pendingPath]);

  return { isConfirming: pendingPath !== null, stay, discard };
}

export interface DirtyNavigationGuardDialogProps {
  isConfirming: boolean;
  title: string;
  description: string;
  stayLabel: string;
  discardLabel: string;
  closeLabel: string;
  onStay: () => void;
  onDiscard: () => void;
}

/** 离开确认 Dialog(stay/discard;discard 为破坏性 → danger 变体)。 */
export function DirtyNavigationGuardDialog(props: DirtyNavigationGuardDialogProps): React.JSX.Element {
  const { isConfirming, title, description, stayLabel, discardLabel, closeLabel, onStay, onDiscard } =
    props;
  return (
    <Dialog open={isConfirming} onClose={onStay} title={title} closeLabel={closeLabel}>
      <p className="mesh-settings-guard__description">{description}</p>
      <div className="mesh-settings-guard__actions">
        <Button variant="secondary" onClick={onStay} data-testid="dirty-guard-stay">
          {stayLabel}
        </Button>
        <Button variant="danger" onClick={onDiscard} data-testid="dirty-guard-discard">
          {discardLabel}
        </Button>
      </div>
    </Dialog>
  );
}
