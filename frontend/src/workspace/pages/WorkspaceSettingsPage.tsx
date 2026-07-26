/**
 * 工作区设置页(/w/:workspaceSlug/settings,workspace.md §4.1/§4.2)。
 *
 * 节区:基本信息(名称/Logo/slug/时区/默认 locale)、邀请(admin+)、成员与角色(admin+)、
 * 危险区(owner)。非 admin 直达本页呈现「无权限」态(§6.12 异常态矩阵;后端 403 兜底)。
 * 基本信息表单:PATCH 浅合并 settings(W4);slug 变更提示旧链接重定向(W6)并规范化导航;
 * 422 unsupported_locale / invalid_timezone、409 slug_taken、400 validation_error 具名呈现。
 */
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { MeshApiError, errorToI18nKey } from '../../api/errors';
import { getApiClient } from '../../api/instance';
import type { WorkspacePatch } from '../../api/workspace';
import { Button, Input, Select } from '../../design';
import { useToast } from '../../design';
import { ApiTokensSettings, AuditSettings } from '../../features/auth';
import { SUPPORTED_LOCALES, useT } from '../../i18n';
import { useSettingsStore } from '../../state/settingsStore';
import { DangerZone } from '../DangerZone';
import { InvitationCreatePanel } from '../InvitationCreatePanel';
import { InvitationList } from '../InvitationList';
import { RolesMatrix } from '../RolesMatrix';
import { isHttpsUrl } from '../permissions';
import { useWorkspace, WorkspaceGate } from '../WorkspaceProvider';

const BASE_TIMEZONES = ['UTC', 'Asia/Shanghai', 'America/New_York', 'Europe/London'];

export function WorkspaceSettingsPage(): React.JSX.Element {
  return (
    <WorkspaceGate>
      <SettingsSections />
    </WorkspaceGate>
  );
}

function SettingsSections(): React.JSX.Element {
  const { workspace, isAdmin, isOwner } = useWorkspace();
  const t = useT();
  const [refreshTick, setRefreshTick] = useState(0);

  if (workspace === null) return <></>;
  if (!isAdmin) {
    return (
      <div className="mesh-ws-settings" data-testid="ws-settings-denied">
        <h2>{t('state.permissionTitle')}</h2>
        <p>{t('state.permissionDescription')}</p>
        <p>{t('state.permissionHint')}</p>
      </div>
    );
  }

  const caps = {
    maxUsesCap:
      typeof workspace.settings.invitation_max_uses_cap === 'number'
        ? workspace.settings.invitation_max_uses_cap
        : 100,
    lifetimeHoursCap:
      typeof workspace.settings.invitation_max_lifetime_hours_cap === 'number'
        ? workspace.settings.invitation_max_lifetime_hours_cap
        : 720,
  };

  return (
    <div className="mesh-ws-settings" data-testid="ws-settings">
      <h1>{t('workspace.settingsTitle')}</h1>
      <BasicInfoSection />
      <section aria-label={t('invitations.sectionTitle')}>
        <h2>{t('invitations.sectionTitle')}</h2>
        <InvitationCreatePanel
          workspaceId={workspace.id}
          caps={caps}
          onCreated={() => setRefreshTick((tick) => tick + 1)}
        />
        <InvitationList workspaceId={workspace.id} refreshSignal={refreshTick} />
      </section>
      <section aria-label={t('roles.sectionTitle')}>
        <h2>{t('roles.sectionTitle')}</h2>
        <RolesMatrix workspaceId={workspace.id} />
      </section>
      {/* label-property.md §4.1:标签与自定义字段管理入口(独立设置子页) */}
      <section aria-label={t('labels.sectionTitle')}>
        <h2>{t('labels.sectionTitle')}</h2>
        <p>
          <Link to={`/w/${workspace.slug}/settings/labels`} data-testid="ws-labels-link">
            {t('labels.pageTitle')}
          </Link>
        </p>
        <p>
          <Link to={`/w/${workspace.slug}/settings/custom-fields`} data-testid="ws-fields-link">
            {t('fields.pageTitle')}
          </Link>
        </p>
      </section>
      {/* auth.md §4.3 API Tokens(明文仅一次)与 §4.4 审计(admin+) */}
      <ApiTokensSettings client={getApiClient()} workspaceId={workspace.id} />
      <AuditSettings client={getApiClient()} workspaceId={workspace.id} />
      {isOwner ? (
        <section aria-label={t('danger.sectionTitle')}>
          <DangerZone workspaceId={workspace.id} workspaceSlug={workspace.slug} />
        </section>
      ) : null}
    </div>
  );
}

function BasicInfoSection(): React.JSX.Element {
  const { workspace, patch } = useWorkspace();
  const t = useT();
  const navigate = useNavigate();
  const { addToast } = useToast();
  const preferences = useSettingsStore((state) => state.preferences);

  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [logoUrl, setLogoUrl] = useState('');
  const [timezone, setTimezone] = useState('');
  const [defaultLocale, setDefaultLocale] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [errorValues, setErrorValues] = useState<Record<string, unknown>>({});
  const [initializedFor, setInitializedFor] = useState<string | null>(null);

  if (workspace === null) return <></>;

  // 工作区(切换/realtime 更新)变化时以服务端值为表单初值。
  if (initializedFor !== workspace.id) {
    setName(workspace.name);
    setSlug(workspace.slug);
    setLogoUrl(workspace.logo_url ?? '');
    setTimezone(workspace.timezone);
    setDefaultLocale(
      typeof workspace.settings.default_locale === 'string'
        ? workspace.settings.default_locale
        : 'en',
    );
    setInitializedFor(workspace.id);
    setErrorKey(null);
  }

  const dirty =
    name.trim() !== workspace.name ||
    slug.trim() !== workspace.slug ||
    logoUrl.trim() !== (workspace.logo_url ?? '') ||
    timezone !== workspace.timezone ||
    defaultLocale !==
      (typeof workspace.settings.default_locale === 'string'
        ? workspace.settings.default_locale
        : 'en');

  const handleSave = async (): Promise<void> => {
    if (logoUrl.trim() !== '' && !isHttpsUrl(logoUrl.trim())) {
      setErrorKey('workspace.logoHttpsOnly');
      return;
    }
    setIsSaving(true);
    setErrorKey(null);
    const changes: WorkspacePatch = {};
    if (name.trim() !== workspace.name) changes.name = name.trim();
    if (slug.trim() !== workspace.slug) changes.slug = slug.trim();
    if (logoUrl.trim() !== (workspace.logo_url ?? '')) {
      changes.logo_url = logoUrl.trim() === '' ? null : logoUrl.trim();
    }
    if (timezone !== workspace.timezone) changes.timezone = timezone;
    const currentLocale =
      typeof workspace.settings.default_locale === 'string'
        ? workspace.settings.default_locale
        : 'en';
    if (defaultLocale !== currentLocale) {
      changes.settings = { default_locale: defaultLocale };
    }
    try {
      const updated = await patch(changes);
      addToast(t('workspace.savedToast'), { tone: 'success', closeLabel: t('a11y.dismiss') });
      if (updated.slug !== workspace.slug) {
        addToast(t('workspace.slugRedirectToast'), {
          tone: 'info',
          closeLabel: t('a11y.dismiss'),
        });
        navigate(`/w/${updated.slug}/settings`, { replace: true });
      }
    } catch (err) {
      if (err instanceof MeshApiError && err.code === 'unsupported_locale') {
        const supported = (err.details ?? {}).supported;
        setErrorKey('workspace.unsupportedLocale');
        setErrorValues({
          supported: Array.isArray(supported)
            ? supported.join(', ')
            : SUPPORTED_LOCALES.join(', '),
        });
      } else if (err instanceof MeshApiError && err.code === 'invalid_timezone') {
        setErrorKey('workspace.invalidTimezone');
        setErrorValues({});
      } else if (err instanceof MeshApiError && err.code === 'slug_taken') {
        setErrorKey('error.slug_taken');
        setErrorValues({});
      } else {
        setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
        setErrorValues({});
      }
    } finally {
      setIsSaving(false);
    }
  };

  const timezoneOptions = Array.from(
    new Set([...BASE_TIMEZONES, preferences.timezone, workspace.timezone]),
  );

  return (
    <section aria-label={t('workspace.basicSection')} data-testid="ws-basic-info">
      <h2>{t('workspace.basicSection')}</h2>
      <Input
        label={t('workspace.nameLabel')}
        value={name}
        maxLength={80}
        data-testid="ws-name-input"
        onChange={(event) => setName(event.target.value)}
      />
      <Input
        label={t('workspace.slugLabel')}
        hint={t('workspace.slugRedirectHint')}
        value={slug}
        data-testid="ws-slug-input"
        onChange={(event) => setSlug(event.target.value)}
      />
      <Input
        label={t('workspace.logoLabel')}
        hint={t('workspace.logoHint')}
        value={logoUrl}
        data-testid="ws-logo-input"
        onChange={(event) => setLogoUrl(event.target.value)}
      />
      <Select
        label={t('workspace.timezoneLabel')}
        value={timezone}
        data-testid="ws-timezone-select"
        onChange={(event) => setTimezone(event.target.value)}
      >
        {timezoneOptions.map((zone) => (
          <option key={zone} value={zone}>
            {zone}
          </option>
        ))}
      </Select>
      <Select
        label={t('workspace.localeLabel')}
        value={defaultLocale}
        data-testid="ws-locale-select"
        onChange={(event) => setDefaultLocale(event.target.value)}
      >
        {SUPPORTED_LOCALES.map((locale) => (
          <option key={locale} value={locale}>
            {locale}
          </option>
        ))}
      </Select>
      <p className="mesh-field-hint">{t('workspace.localeHint')}</p>
      {errorKey !== null ? (
        <p role="alert" data-testid="ws-basic-error">
          {t(errorKey, errorValues)}
        </p>
      ) : null}
      <Button
        data-testid="ws-save"
        disabled={!dirty}
        isLoading={isSaving}
        onClick={() => void handleSave()}
      >
        {t('common.save')}
      </Button>
    </section>
  );
}
