/**
 * 工作区选择页(search-command-palette.md §3.4 解析序 ⑤)。
 *
 * 多工作区用户无上下文访问旧扁平路由/规范入口且无法自动解析 active
 * workspace 时落此页:列出所属工作区卡片,选定后记忆 last_workspace 并
 * 跳规范路由;`?next=` 保留原意图路径(仅接受站内 `/` 开头相对路径,
 * 防开放重定向)。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { getApiClient } from '../api/instance';
import { ErrorState, Skeleton } from '../design';
import { fetchMe } from '../features/members/api';
import type { Membership } from '../features/members/types';
import { useT } from '../i18n';
import { recordLastWorkspace } from './lastWorkspace';

type LoadStatus = 'loading' | 'ready' | 'error';

/** next 参数只接受站内 `/` 开头相对路径(防 protocol-relative / 绝对 URL 开放重定向)。 */
function safeNextPath(raw: string | null): string | null {
  if (raw === null) return null;
  if (!raw.startsWith('/') || raw.startsWith('//')) return null;
  return raw;
}

export function WorkspacePickerPage(): React.JSX.Element {
  const t = useT();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState<LoadStatus>('loading');
  const [userId, setUserId] = useState<string | null>(null);
  const [memberships, setMemberships] = useState<readonly Membership[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const me = await fetchMe(getApiClient());
        if (cancelled) return;
        setUserId(me.user.id);
        setMemberships(me.memberships);
        setStatus('ready');
      } catch {
        if (!cancelled) setStatus('error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = useCallback(
    (membership: Membership) => {
      if (userId !== null) {
        recordLastWorkspace(userId, membership.workspace_slug);
      }
      const next = safeNextPath(searchParams.get('next'));
      navigate(next ?? `/w/${membership.workspace_slug}/inbox`, { replace: true });
    },
    [navigate, searchParams, userId],
  );

  if (status === 'loading') {
    return (
      <div className="mesh-page" data-testid="ws-picker-loading">
        <Skeleton loadingLabel={t('common.loading')} />
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className="mesh-page" data-testid="ws-picker-error">
        <ErrorState
          title={t('state.errorTitle')}
          description={t('state.errorDescription')}
          retryLabel={t('common.retry')}
          onRetry={() => navigate(0)}
        />
      </div>
    );
  }

  return (
    <div className="mesh-page" data-testid="workspace-picker">
      <h1 className="mesh-page__title">{t('workspacePicker.title')}</h1>
      <p className="mesh-ws-picker__hint">{t('workspacePicker.hint')}</p>
      <ul className="mesh-ws-picker__list">
        {memberships.map((membership) => (
          <li key={membership.workspace_id} className="mesh-ws-picker__item">
            <button
              type="button"
              className="mesh-ws-picker__card"
              data-testid={`ws-picker-${membership.workspace_slug}`}
              onClick={() => choose(membership)}
            >
              <span className="mesh-ws-picker__name">{membership.workspace_name}</span>
              <span className="mesh-ws-picker__slug">/{membership.workspace_slug}</span>
              <span className="mesh-ws-picker__role">{t('members.role.' + membership.role)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
