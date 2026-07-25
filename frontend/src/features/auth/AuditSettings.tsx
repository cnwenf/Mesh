/**
 * 设置 → 审计(auth.md §4.4 / §3.3 / §5.3,admin+):按 action / 时间范围
 * (before/after,RFC3339)过滤 + 游标分页。只读。工作区上下文。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api/client';
import { listAuditLogs } from '../../api';
import type { AuditLogEntry } from '../../api';
import { Button, Input } from '../../design';
import { useT } from '../../i18n';

const PAGE_SIZE = 20;

export interface AuditSettingsProps {
  client: MeshApiClient;
  workspaceId: string;
}

export function AuditSettings(props: AuditSettingsProps): React.JSX.Element {
  const { client, workspaceId } = props;
  const t = useT();

  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState('');
  const [after, setAfter] = useState('');
  const [before, setBefore] = useState('');

  const load = useCallback(
    (nextCursor: string | null, replace: boolean) => {
      void listAuditLogs(client, workspaceId, {
        action: actionFilter.trim() === '' ? undefined : actionFilter.trim(),
        after: after.trim() === '' ? undefined : after.trim(),
        before: before.trim() === '' ? undefined : before.trim(),
        limit: PAGE_SIZE,
        cursor: nextCursor ?? undefined,
      })
        .then((page) => {
          setEntries((prev) => (replace ? page.data : [...prev, ...page.data]));
          setCursor(page.next_cursor);
        })
        .catch(() => {
          if (replace) setEntries([]);
          setCursor(null);
        });
    },
    [client, workspaceId, actionFilter, after, before],
  );

  useEffect(() => {
    load(null, true);
    // 仅在过滤条件变化时重载;load 依赖已含过滤项。
  }, [load]);

  return (
    <div className="mesh-settings__group">
      <h3 className="mesh-settings__heading">{t('audit.title')}</h3>
      <p className="mesh-settings__hint">{t('audit.description')}</p>

      <div className="mesh-audit__filters" data-testid="audit-filters">
        <Input
          data-testid="audit-action"
          label={t('audit.filterAction')}
          value={actionFilter}
          onChange={(event) => setActionFilter(event.target.value)}
        />
        <Input
          data-testid="audit-after"
          label={t('audit.after')}
          value={after}
          placeholder="2026-07-01T00:00:00Z"
          onChange={(event) => setAfter(event.target.value)}
        />
        <Input
          data-testid="audit-before"
          label={t('audit.before')}
          value={before}
          placeholder="2026-08-01T00:00:00Z"
          onChange={(event) => setBefore(event.target.value)}
        />
        <Button variant="secondary" data-testid="audit-apply" onClick={() => load(null, true)}>
          {t('audit.applyFilter')}
        </Button>
      </div>

      {entries.length === 0 ? (
        <p data-testid="audit-empty">{t('audit.empty')}</p>
      ) : (
        <table className="mesh-audit__table" data-testid="audit-table">
          <thead>
            <tr>
              <th>{t('audit.time')}</th>
              <th>{t('audit.action')}</th>
              <th>{t('audit.actor')}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id} data-testid={`audit-${entry.id}`}>
                <td>{entry.created_at}</td>
                <td>{entry.action}</td>
                <td>
                  {entry.actor_kind === 'system'
                    ? t('audit.actorSystem')
                    : (entry.actor_member_id ?? '')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {cursor !== null ? (
        <Button
          variant="secondary"
          data-testid="audit-load-more"
          onClick={() => load(cursor, false)}
        >
          {t('audit.loadMore')}
        </Button>
      ) : null}
    </div>
  );
}
