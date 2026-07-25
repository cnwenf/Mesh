/**
 * 首页骨架演示区(e2e 主舞台;文案一律经消息目录 home.* / demo.*)。
 *
 * 1. demoTheme — 主题即时切换;
 * 2. demoLocale — locale 即时切换 + ICU 复数示例 + 相对时间示例;
 * 3. demoShortcuts — 快捷键一览(均注明有等价鼠标路径,§6.12);
 * 4. demoStates — 异常态矩阵三态(loading/empty/retry);
 * 5. demoRealtime — 实时增量合并演示:未登录(shell 外/无 token)显示提示;
 *    有 client 时订阅演示频道、游标分页播种、帧合并、创建、乐观重命名(If-Match + 409 收敛)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import {
  errorToI18nKey,
  getToken,
  MeshApiClient,
  MeshApiError,
  useCursorPagination,
  useOptimisticMutation,
} from '../../api';
import { Button, EmptyState, ErrorState, Input, Kbd, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { formatRelativeTime, useT } from '../../i18n';
import { mergeEntityFrame } from '../../realtime';
import type { RealtimeClient } from '../../realtime';
import { useSettingsStore } from '../../state/settingsStore';
import type { ThemeMode } from '../../state/settingsStore';
import type { IssueSummary } from '../../types/entities';
import { formatCombo } from '../../shortcuts';
import { useRealtimeContext } from '../AppShell';

const DEMO_ISSUES_PATH = '/api/v1/demo/issues';
/** 演示频道:真实后端联调时经 VITE_MESH_DEMO_CHANNEL 指向 workspace:<uuid>:issues */
const DEMO_CHANNEL = env.demoChannel;
const RELATIVE_SAMPLE_OFFSET_MS = 3 * 60 * 1000;

export function HomePage(): React.JSX.Element {
  const t = useT();
  const apiClient = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  return (
    <div className="mesh-page mesh-home">
      <header className="mesh-home__hero">
        <h1 className="mesh-page__title">{t('home.title')}</h1>
        <p className="mesh-home__subtitle">{t('home.subtitle')}</p>
      </header>
      <ThemeDemo />
      <LocaleDemo />
      <ShortcutsDemo />
      <StatesDemo />
      <section className="mesh-home__section" data-testid="demo-realtime" aria-label={t('home.demoRealtime')}>
        <h2 className="mesh-home__heading">{t('home.demoRealtime')}</h2>
        <RealtimeSection apiClient={apiClient} />
      </section>
    </div>
  );
}

function ThemeDemo(): React.JSX.Element {
  const t = useT();
  const setTheme = useSettingsStore((state) => state.setTheme);
  const modes: ReadonlyArray<ThemeMode> = ['light', 'dark', 'system'];
  return (
    <section className="mesh-home__section" data-testid="demo-theme" aria-label={t('home.demoTheme')}>
      <h2 className="mesh-home__heading">{t('home.demoTheme')}</h2>
      <div className="mesh-home__row">
        {modes.map((mode) => (
          <Button key={mode} data-testid={'demo-theme-' + mode} variant="secondary" onClick={() => setTheme(mode)}>
            {t('theme.' + mode)}
          </Button>
        ))}
      </div>
    </section>
  );
}

function LocaleDemo(): React.JSX.Element {
  const t = useT();
  const locale = useSettingsStore((state) => state.preferences.locale);
  const setLocale = useSettingsStore((state) => state.setLocale);
  const activeLocale = locale ?? 'en';
  const [count, setCount] = useState(3);

  const handleCountChange = (event: FormEvent<HTMLInputElement>): void => {
    const parsed = Number.parseInt(event.currentTarget.value, 10);
    setCount(Number.isNaN(parsed) ? 0 : parsed);
  };

  const threeMinutesAgo = new Date(Date.now() - RELATIVE_SAMPLE_OFFSET_MS).toISOString();

  return (
    <section className="mesh-home__section" data-testid="demo-locale" aria-label={t('home.demoLocale')}>
      <h2 className="mesh-home__heading">{t('home.demoLocale')}</h2>
      <div className="mesh-home__row">
        <Button data-testid="demo-locale-zh" variant="secondary" onClick={() => setLocale('zh-CN')}>
          zh-CN
        </Button>
        <Button data-testid="demo-locale-en" variant="secondary" onClick={() => setLocale('en')}>
          en
        </Button>
        <Button data-testid="demo-locale-default" variant="secondary" onClick={() => setLocale(null)}>
          {t('settings.languageFollowDefault')}
        </Button>
      </div>
      <div className="mesh-home__row">
        <Input
          data-testid="demo-count"
          type="number"
          min={0}
          label={t('demo.countLabel')}
          value={count}
          onChange={handleCountChange}
        />
        <p className="mesh-home__sample" data-testid="demo-icu">
          {t('demo.commentCount', { count })}
        </p>
        <p className="mesh-home__sample" data-testid="demo-relative">
          {formatRelativeTime(threeMinutesAgo, { locale: activeLocale })}
        </p>
      </div>
    </section>
  );
}

function ShortcutsDemo(): React.JSX.Element {
  const t = useT();
  return (
    <section className="mesh-home__section" data-testid="demo-shortcuts" aria-label={t('home.demoShortcuts')}>
      <h2 className="mesh-home__heading">{t('home.demoShortcuts')}</h2>
      <ul className="mesh-home__shortcut-list">
        <li>
          <Kbd>{formatCombo('mod+k')}</Kbd> <span>{t('shortcuts.actionPalette')}</span>
        </li>
        <li>
          <Kbd>?</Kbd> <span>{t('shortcuts.actionHelp')}</span>
        </li>
        <li>
          <Kbd>/</Kbd> <span>{t('shortcuts.actionFocusSearch')}</span>
        </li>
        <li>
          <Kbd>C</Kbd> <span>{t('shortcuts.actionNewIssue')}</span>
        </li>
        <li>
          <Kbd>G</Kbd> <Kbd>I</Kbd> <span>{t('shortcuts.actionGoInbox')}</span>
        </li>
        <li>
          <Kbd>G</Kbd> <Kbd>B</Kbd> <span>{t('shortcuts.actionGoBoard')}</span>
        </li>
        <li>
          <Kbd>G</Kbd> <Kbd>M</Kbd> <span>{t('shortcuts.actionGoMembers')}</span>
        </li>
        <li>
          <Kbd>G</Kbd> <Kbd>A</Kbd> <span>{t('shortcuts.actionGoAutomation')}</span>
        </li>
      </ul>
      <p className="mesh-home__hint">{t('home.shortcutsMouseNote')}</p>
    </section>
  );
}

function StatesDemo(): React.JSX.Element {
  const t = useT();
  const { addToast } = useToast();
  const handleRetry = (): void => {
    addToast(t('state.retryHint'), { tone: 'info', closeLabel: t('a11y.dismiss') });
  };
  return (
    <section className="mesh-home__section" data-testid="demo-states" aria-label={t('home.demoStates')}>
      <h2 className="mesh-home__heading">{t('home.demoStates')}</h2>
      <div className="mesh-home__states">
        <Skeleton loadingLabel={t('state.loading')} />
        <EmptyState title={t('state.emptyTitle')} description={t('state.emptyDescription')} />
        <ErrorState
          title={t('state.errorTitle')}
          description={t('state.errorDescription')}
          retryLabel={t('common.retry')}
          onRetry={handleRetry}
        />
      </div>
    </section>
  );
}

interface RealtimeSectionProps {
  apiClient: MeshApiClient;
}

function RealtimeSection(props: RealtimeSectionProps): React.JSX.Element {
  const t = useT();
  const realtime = useRealtimeContext();
  if (realtime === null) {
    return (
      <p className="mesh-home__hint" data-testid="demo-realtime-hint">
        {t('home.realtimeLoginHint')}
      </p>
    );
  }
  return <RealtimeDemo apiClient={props.apiClient} client={realtime.client} />;
}

interface RealtimeDemoProps {
  apiClient: MeshApiClient;
  client: RealtimeClient;
}

function RealtimeDemo(props: RealtimeDemoProps): React.JSX.Element {
  const t = useT();
  const { apiClient, client } = props;
  const { addToast } = useToast();
  const [issues, setIssues] = useState<ReadonlyMap<string, IssueSummary>>(() => new Map());
  const [newTitle, setNewTitle] = useState('');

  const page = useCursorPagination<IssueSummary>((cursor) =>
    apiClient.list<IssueSummary>(DEMO_ISSUES_PATH, { query: cursor !== null ? { cursor } : undefined }),
  );

  // 游标分页结果播种进本地 Map(帧合并与分页共用同一份数据)
  useEffect(() => {
    setIssues((prev) => {
      const next = new Map(prev);
      for (const item of page.items) next.set(item.id, item);
      return next;
    });
  }, [page.items]);

  // 订阅演示频道;帧经 mergeEntityFrame 增量合并(演示频道 belongs 恒真)
  useEffect(() => {
    client.subscribe(DEMO_CHANNEL);
    const unsubscribeFrame = client.onFrame((frame) => {
      setIssues((prev) => mergeEntityFrame(prev, frame, { belongs: () => true }));
    });
    return () => {
      unsubscribeFrame();
      client.unsubscribe(DEMO_CHANNEL);
    };
  }, [client]);

  const reportError = useCallback(
    (error: unknown): void => {
      const key = error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown';
      addToast(t(key), { tone: 'danger', closeLabel: t('a11y.dismiss') });
    },
    [addToast, t],
  );

  const upsertIssue = useCallback((issue: IssueSummary): void => {
    setIssues((prev) => {
      const next = new Map(prev);
      next.set(issue.id, issue);
      return next;
    });
  }, []);

  const handleCreate = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const title = newTitle.trim();
    if (title.length === 0) return;
    try {
      const created = await apiClient.request<IssueSummary>('POST', DEMO_ISSUES_PATH, { body: { title } });
      upsertIssue(created);
      setNewTitle('');
    } catch (error) {
      reportError(error);
    }
  };

  const rows = [...issues.values()];

  // 真实后端无 /demo/issues 演示端点 → 404;此时不渲染交互式演示表单(避免全局
  // 首页出现误导性的错误/空态),仅保留实时频道订阅并提示(§4.5 实时仍可用)。
  const demoUnavailable = page.error !== null && rows.length === 0;
  if (demoUnavailable) {
    return (
      <p className="mesh-home__hint" data-testid="demo-realtime-unavailable">
        {t('home.demoUnavailable')}
      </p>
    );
  }

  return (
    <div className="mesh-home__realtime">
      <form className="mesh-home__row" onSubmit={(event) => void handleCreate(event)}>
        <Input
          data-testid="demo-new-title"
          label={t('home.newIssueTitle')}
          value={newTitle}
          onChange={(event) => setNewTitle(event.target.value)}
        />
        <Button data-testid="demo-create" type="submit">
          {t('home.createIssue')}
        </Button>
      </form>
      {page.hasMore ? (
        <Button data-testid="demo-load-more" variant="secondary" onClick={() => void page.fetchNext()}>
          {t('home.loadMore')}
        </Button>
      ) : null}
      <ul className="mesh-home__issue-list" data-testid="demo-issue-list">
        {rows.map((issue) => (
          <IssueRow
            key={issue.id}
            issue={issue}
            apiClient={apiClient}
            onUpdated={upsertIssue}
            onError={reportError}
          />
        ))}
      </ul>
    </div>
  );
}

interface IssueRowProps {
  issue: IssueSummary;
  apiClient: MeshApiClient;
  onUpdated: (issue: IssueSummary) => void;
  onError: (error: unknown) => void;
}

function IssueRow(props: IssueRowProps): React.JSX.Element {
  const t = useT();
  const { issue, apiClient, onUpdated, onError } = props;
  const { mutate } = useOptimisticMutation<IssueSummary>({
    client: apiClient,
    path: DEMO_ISSUES_PATH + '/' + issue.id,
    getServerVersion: (versioned) => versioned.updated_at,
  });

  const handleRename = async (): Promise<void> => {
    try {
      const { result } = await mutate(issue, { title: issue.title + ' ✓' });
      onUpdated(result);
    } catch (error) {
      onError(error);
    }
  };

  return (
    <li className="mesh-home__issue" data-testid={'demo-issue-' + issue.identifier}>
      <span className="mesh-home__issue-key">{issue.identifier}</span>
      <span className="mesh-home__issue-title">{issue.title}</span>
      <Button
        data-testid={'demo-rename-' + issue.identifier}
        size="sm"
        variant="secondary"
        onClick={() => void handleRename()}
      >
        {t('home.renameIssue')}
      </Button>
    </li>
  );
}
