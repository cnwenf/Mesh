/**
 * 通知偏好设置(comment-inbox.md §4.2 / I11,Settings → Notifications):
 * 矩阵表格(行=事件类型 × 站内开关 + 邮件策略 无/实时/摘要);「Agent 执行通知」
 * (execution_finished)单独分区;全局免打扰时段(quiet hours,标注 critical 穿透)。
 * 读取 GET / PUT /notification-preferences(workspace_id 经 useInboxContext 解析)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { NOTIFICATION_TYPES, getPreferences, updatePreferences } from './api';
import type { EmailPolicy, PreferenceInput } from './types';
import { useInboxContext } from './useInboxContext';

interface RowState {
  readonly in_app: boolean;
  readonly email: EmailPolicy;
}

const DEFAULT_ROW: RowState = { in_app: true, email: 'digest' };

/** Agent 执行通知单列分区(核心差异);其余事件归常规矩阵。 */
const AGENT_EVENT = 'execution_finished';

export function NotificationPreferencesSection(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const { workspaceId } = useInboxContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [rows, setRows] = useState<Readonly<Record<string, RowState>>>({});
  const [quietStart, setQuietStart] = useState('');
  const [quietEnd, setQuietEnd] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    setIsLoading(true);
    void (async () => {
      try {
        const prefs = await getPreferences(client, workspaceId);
        if (cancelled) return;
        const next: Record<string, RowState> = {};
        for (const type of NOTIFICATION_TYPES) next[type] = DEFAULT_ROW;
        for (const pref of prefs) {
          next[pref.event_type] = { in_app: pref.in_app, email: pref.email };
          if (pref.quiet_hours_start !== null) setQuietStart(pref.quiet_hours_start);
          if (pref.quiet_hours_end !== null) setQuietEnd(pref.quiet_hours_end);
        }
        setRows(next);
      } catch {
        if (!cancelled) {
          const fallback: Record<string, RowState> = {};
          for (const type of NOTIFICATION_TYPES) fallback[type] = DEFAULT_ROW;
          setRows(fallback);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId]);

  const setRow = useCallback((eventType: string, patch: Partial<RowState>) => {
    setRows((prev) => ({
      ...prev,
      [eventType]: { ...(prev[eventType] ?? DEFAULT_ROW), ...patch },
    }));
  }, []);

  const save = useCallback(async () => {
    if (workspaceId === null) return;
    setSaving(true);
    const preferences: PreferenceInput[] = NOTIFICATION_TYPES.map((type) => ({
      event_type: type,
      in_app: rows[type]?.in_app ?? DEFAULT_ROW.in_app,
      email: rows[type]?.email ?? DEFAULT_ROW.email,
      quiet_hours_start: quietStart === '' ? null : quietStart,
      quiet_hours_end: quietEnd === '' ? null : quietEnd,
    }));
    try {
      await updatePreferences(client, workspaceId, preferences);
      toast.addToast(t('notifications.savedToast'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setSaving(false);
    }
  }, [client, workspaceId, rows, quietStart, quietEnd, toast, t]);

  if (isLoading) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }

  const regularTypes = NOTIFICATION_TYPES.filter((type) => type !== AGENT_EVENT);

  const renderRow = (eventType: string): React.JSX.Element => {
    const row = rows[eventType] ?? DEFAULT_ROW;
    return (
      <tr key={eventType} data-testid={`pref-row-${eventType}`}>
        <td>{t(`notifications.type.${eventType}`)}</td>
        <td>
          <input
            type="checkbox"
            aria-label={t('notifications.inApp')}
            data-testid={`pref-inapp-${eventType}`}
            checked={row.in_app}
            onChange={(event) => setRow(eventType, { in_app: event.target.checked })}
          />
        </td>
        <td>
          <select
            aria-label={t('notifications.emailPolicy')}
            data-testid={`pref-email-${eventType}`}
            value={row.email}
            onChange={(event) => setRow(eventType, { email: event.target.value as EmailPolicy })}
          >
            <option value="none">{t('notifications.email.none')}</option>
            <option value="realtime">{t('notifications.email.realtime')}</option>
            <option value="digest">{t('notifications.email.digest')}</option>
          </select>
        </td>
      </tr>
    );
  };

  return (
    <section
      className="mesh-notif-prefs"
      aria-label={t('notifications.title')}
      data-testid="notification-prefs"
    >
      <h3>{t('notifications.title')}</h3>
      <table className="mesh-notif-prefs__table">
        <caption className="sr-only">{t('notifications.title')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('notifications.col.event')}</th>
            <th scope="col">{t('notifications.inApp')}</th>
            <th scope="col">{t('notifications.email')}</th>
          </tr>
        </thead>
        <tbody>{regularTypes.map((type) => renderRow(type))}</tbody>
      </table>

      <h4 className="mesh-notif-prefs__agent-heading">{t('notifications.agentSection')}</h4>
      <table className="mesh-notif-prefs__table">
        <caption className="sr-only">{t('notifications.agentSection')}</caption>
        <thead>
          <tr className="sr-only">
            <th scope="col">{t('notifications.col.event')}</th>
            <th scope="col">{t('notifications.inApp')}</th>
            <th scope="col">{t('notifications.email')}</th>
          </tr>
        </thead>
        <tbody>{renderRow(AGENT_EVENT)}</tbody>
      </table>

      <div className="mesh-notif-prefs__quiet">
        <h4>{t('notifications.quietHours')}</h4>
        <label>
          {t('notifications.quietStart')}
          <input
            type="time"
            data-testid="pref-quiet-start"
            value={quietStart}
            onChange={(event) => setQuietStart(event.target.value)}
          />
        </label>
        <label>
          {t('notifications.quietEnd')}
          <input
            type="time"
            data-testid="pref-quiet-end"
            value={quietEnd}
            onChange={(event) => setQuietEnd(event.target.value)}
          />
        </label>
        <p className="mesh-notif-prefs__quiet-note">{t('notifications.quietNote')}</p>
      </div>

      <Button data-testid="pref-save" isLoading={saving} onClick={() => void save()}>
        {t('common.save')}
      </Button>
    </section>
  );
}
