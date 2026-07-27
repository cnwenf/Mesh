/**
 * 技能市场页(skill.md §4.1/§4.2/K10):卡片(下载量/评分/维护方认证)+ 预览 + 导入。
 * 含脚本的第三方技能在导入入口提示「需人工审批」(§4.2)。市场列表是外部来源
 * (§1.3:仅消费,不运营市场)。
 */
import { useEffect, useMemo, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { Button, Dialog, EmptyState, ErrorState, Input, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import { listMarketplace } from './api';
import { ImportWizard } from './ImportWizard';
import type { MarketplaceEntry } from './types';
import './skills.css';

export function MarketplacePage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [membership, setMembership] = useState<Membership | null>(null);
  const [entries, setEntries] = useState<MarketplaceEntry[]>([]);
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importEntry, setImportEntry] = useState<MarketplaceEntry | null>(null);
  const [previewEntry, setPreviewEntry] = useState<MarketplaceEntry | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (!cancelled) setMembership(activeWorkspace(me.memberships));
      })
      .catch(() => {
        /* keep empty state when there is no membership */
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  const workspaceId = membership?.workspace_id ?? null;
  const canManage = membership?.role === 'admin' || membership?.role === 'owner';

  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    setLoading(true);
    listMarketplace(client, workspaceId, { q: q.trim() === '' ? undefined : q.trim(), limit: 50 })
      .then((page) => {
        if (!cancelled) {
          setEntries(page.data);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError(t('skills.marketplaceLoadError'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, q, t]);

  return (
    <div className="mesh-skills">
      <header className="mesh-skills__header">
        <h1 data-testid="marketplace-title">{t('skills.marketplaceTitle')}</h1>
        <div className="mesh-skills__toolbar">
          <Input
            label={t('skills.searchPlaceholder')}
            placeholder={t('skills.searchPlaceholder')}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </header>

      {loading ? (
        <Skeleton loadingLabel={t('state.loading')} />
      ) : error !== null ? (
        <ErrorState title={t('state.errorTitle')} description={error} />
      ) : entries.length === 0 ? (
        <EmptyState title={t('skills.marketplaceEmptyTitle')} description={t('skills.marketplaceEmptyDescription')} />
      ) : (
        <ul className="mesh-skills__grid" data-testid="marketplace-grid">
          {entries.map((entry) => (
            <li key={entry.id} className="mesh-skills__card" data-testid={`market-entry-${entry.id}`}>
              <span className="mesh-skills__card-name">
                {entry.certified ? <span title={t('skills.certified')}>✅ </span> : null}
                {entry.name}
              </span>
              <span className="mesh-skills__card-summary">{entry.summary}</span>
              <span className="mesh-skills__card-meta">
                <span>v{entry.version}</span>
                <span>⬇ {entry.downloads}</span>
                <span>★ {entry.rating.toFixed(1)}</span>
                {entry.has_scripts ? <span className="mesh-skills__script-flag">⚠ {t('skills.hasScripts')}</span> : null}
              </span>
              <div className="mesh-skills__card-actions">
                {entry.has_scripts ? (
                  <span className="mesh-skills__needs-review">{t('skills.marketplaceNeedsReview')}</span>
                ) : null}
                <Button
                  variant="secondary"
                  onClick={() => setPreviewEntry(entry)}
                  data-testid={`market-preview-${entry.id}`}
                >
                  {t('skills.marketplacePreview')}
                </Button>
                {canManage ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      if (entry.manifest_url === '') {
                        toast.addToast(t('skills.marketplaceNoManifest'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
                        return;
                      }
                      setImportEntry(entry);
                    }}
                    data-testid={`market-import-${entry.id}`}
                  >
                    {t('skills.marketplaceImport')}
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      {importEntry !== null && workspaceId !== null ? (
        <ImportWizard
          workspaceId={workspaceId}
          initialUri={importEntry.manifest_url}
          initialSourceType="marketplace"
          onClose={() => setImportEntry(null)}
          onDone={() => setImportEntry(null)}
        />
      ) : null}

      <Dialog
        open={previewEntry !== null}
        onClose={() => setPreviewEntry(null)}
        title={previewEntry ? previewEntry.name : ''}
        closeLabel={t('a11y.closeDialog')}
      >
        {previewEntry !== null ? (
          <div className="mesh-skills__market-preview" data-testid="market-preview-dialog">
            <p>{previewEntry.summary}</p>
            <dl>
              <dt>{t('skills.marketplaceVersion')}</dt>
              <dd>v{previewEntry.version}</dd>
              <dt>{t('skills.marketplaceDownloads')}</dt>
              <dd>{previewEntry.downloads}</dd>
              <dt>{t('skills.marketplaceRating')}</dt>
              <dd>★ {previewEntry.rating.toFixed(1)}</dd>
              <dt>{t('skills.marketplaceCertified')}</dt>
              <dd>{previewEntry.certified ? t('skills.marketplaceYes') : t('skills.marketplaceNo')}</dd>
              <dt>{t('skills.marketplaceScripts')}</dt>
              <dd>{previewEntry.has_scripts ? `⚠ ${t('skills.hasScripts')}` : t('skills.marketplaceNo')}</dd>
            </dl>
            {canManage ? (
              <Button
                onClick={() => {
                  const e = previewEntry;
                  setPreviewEntry(null);
                  if (e.manifest_url === '') {
                    toast.addToast(t('skills.marketplaceNoManifest'), { tone: 'danger', closeLabel: t('a11y.closeDialog') });
                    return;
                  }
                  setImportEntry(e);
                }}
                data-testid="market-preview-import"
              >
                {t('skills.marketplaceImport')}
              </Button>
            ) : null}
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
