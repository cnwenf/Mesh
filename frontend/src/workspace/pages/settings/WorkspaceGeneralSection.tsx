/**
 * 工作区设置 → 通用(/w/:slug/settings/general,workspace.md §4.2)。
 *
 * 基本信息表单:名称/slug/Logo URL/时区/默认 locale + **G11 默认主题**(theme.md §4.1,
 * admin 可见,文案「成员未单独设置时生效」= workspace.defaultThemeHint)。
 * - dirty state(不可变比较):pristine 禁用保存;保存按钮 loading 保持宽度;
 * - 成功 → toast;失败 → 具名错误(errorToI18nKey / 422 unsupported_locale 等),值不丢;
 * - slug 变更 → 重定向提示 + 规范化导航(W6);
 * - 脏态离开 → useDirtyNavigationGuard 确认(stay/discard)。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { MeshApiError, errorToI18nKey } from '../../../api/errors';
import type { WorkspaceDetail, WorkspacePatch } from '../../../api/workspace';
import { Button, Input, Select, SettingsSection, useToast } from '../../../design';
import { SUPPORTED_LOCALES, useT } from '../../../i18n';
import { useSettingsStore } from '../../../state/settingsStore';
import { isHttpsUrl } from '../../permissions';
import { useWorkspace } from '../../WorkspaceProvider';
import { deriveWorkspaceFeatureFlags } from '../../featureFlags';
import { DirtyNavigationGuardDialog, useDirtyNavigationGuard } from '../../useDirtyNavigationGuard';

const BASE_TIMEZONES = ['UTC', 'Asia/Shanghai', 'America/New_York', 'Europe/London'];

interface SaveError {
  key: string;
  values: Record<string, unknown>;
}

/** 读取工作区当前 settings 中的 default_locale / default_theme(归一默认值)。 */
function currentSettings(workspace: WorkspaceDetail): {
  locale: string;
  theme: string;
  autopilot: boolean;
} {
  return {
    locale:
      typeof workspace.settings.default_locale === 'string'
        ? workspace.settings.default_locale
        : 'en',
    theme:
      typeof workspace.settings.default_theme === 'string'
        ? workspace.settings.default_theme
        : 'system',
    autopilot: deriveWorkspaceFeatureFlags(workspace.settings).autopilot,
  };
}

/** 将保存异常归一为 i18n 键 + 插值(§6.14 具名 code)。 */
function resolveSaveError(err: unknown): SaveError {
  if (err instanceof MeshApiError && err.code === 'unsupported_locale') {
    const supported = (err.details ?? {}).supported;
    return {
      key: 'workspace.unsupportedLocale',
      values: {
        supported: Array.isArray(supported) ? supported.join(', ') : SUPPORTED_LOCALES.join(', '),
      },
    };
  }
  if (err instanceof MeshApiError && err.code === 'invalid_timezone') {
    return { key: 'workspace.invalidTimezone', values: {} };
  }
  if (err instanceof MeshApiError && err.code === 'slug_taken') {
    return { key: 'error.slug_taken', values: {} };
  }
  return { key: err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown', values: {} };
}

export function WorkspaceGeneralSection(): React.JSX.Element {
  const { workspace, patch } = useWorkspace();
  const t = useT();
  const navigate = useNavigate();
  const { addToast } = useToast();
  const preferences = useSettingsStore((state) => state.preferences);

  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [logoUrl, setLogoUrl] = useState('');
  const [timezone, setTimezone] = useState('');
  const [defaultLocale, setDefaultLocale] = useState('en');
  const [defaultTheme, setDefaultTheme] = useState('system');
  const [autopilotEnabled, setAutopilotEnabled] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [errorValues, setErrorValues] = useState<Record<string, unknown>>({});
  const [initializedFor, setInitializedFor] = useState<string | null>(null);

  // dirty 与离开守卫:hooks 必须无条件调用(置于 workspace 空值早退之前)。
  const baseline = workspace !== null ? currentSettings(workspace) : null;
  const dirty =
    workspace !== null &&
    baseline !== null &&
    (name.trim() !== workspace.name ||
      slug.trim() !== workspace.slug ||
      logoUrl.trim() !== (workspace.logo_url ?? '') ||
      timezone !== workspace.timezone ||
      defaultLocale !== baseline.locale ||
      defaultTheme !== baseline.theme ||
      autopilotEnabled !== baseline.autopilot);
  const guard = useDirtyNavigationGuard(dirty);

  if (workspace === null) return <></>;

  // 工作区(切换/realtime 更新)变化时以服务端值为表单初值。
  if (initializedFor !== workspace.id) {
    const base = currentSettings(workspace);
    setName(workspace.name);
    setSlug(workspace.slug);
    setLogoUrl(workspace.logo_url ?? '');
    setTimezone(workspace.timezone);
    setDefaultLocale(base.locale);
    setDefaultTheme(base.theme);
    setAutopilotEnabled(base.autopilot);
    setInitializedFor(workspace.id);
    setErrorKey(null);
  }

  const handleSave = async (): Promise<void> => {
    if (logoUrl.trim() !== '' && !isHttpsUrl(logoUrl.trim())) {
      setErrorKey('workspace.logoHttpsOnly');
      return;
    }
    setIsSaving(true);
    setErrorKey(null);
    const changes = buildChanges(workspace, {
      name,
      slug,
      logoUrl,
      timezone,
      defaultLocale,
      defaultTheme,
      autopilotEnabled,
    });
    try {
      const updated = await patch(changes);
      addToast(t('workspace.savedToast'), { tone: 'success', closeLabel: t('a11y.dismiss') });
      if (updated.slug !== workspace.slug) {
        addToast(t('workspace.slugRedirectToast'), { tone: 'info', closeLabel: t('a11y.dismiss') });
        navigate(`/w/${updated.slug}/settings/general`, { replace: true });
      }
    } catch (err) {
      const resolved = resolveSaveError(err);
      setErrorKey(resolved.key);
      setErrorValues(resolved.values);
    } finally {
      setIsSaving(false);
    }
  };

  const timezoneOptions = Array.from(
    new Set([...BASE_TIMEZONES, preferences.timezone, workspace.timezone]),
  );

  return (
    <SettingsSection
      title={t('workspace.basicSection')}
      footer={
        <>
          {dirty ? (
            <span className="mesh-settings__hint">{t('workspaceSettings.unsavedHint')}</span>
          ) : null}
          <Button
            data-testid="ws-save"
            disabled={!dirty}
            isLoading={isSaving}
            onClick={() => void handleSave()}
          >
            {t('common.save')}
          </Button>
        </>
      }
    >
      <div data-testid="ws-basic-info">
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
        {/* G11(theme.md §4.1):工作区默认主题入口,admin 可见(本页已在 admin 门控内),
            写 settings.default_theme;hint「成员未单独设置时生效」。 */}
        <Select
          label={t('workspace.defaultTheme')}
          value={defaultTheme}
          data-testid="ws-default-theme-select"
          onChange={(event) => setDefaultTheme(event.target.value)}
        >
          <option value="light">{t('theme.light')}</option>
          <option value="dark">{t('theme.dark')}</option>
          <option value="system">{t('theme.system')}</option>
        </Select>
        <p className="mesh-field-hint" data-testid="ws-default-theme-hint">
          {t('workspace.defaultThemeHint')}
        </p>
        <fieldset className="mesh-settings__fieldset">
          <legend>{t('workspace.featureFlagsTitle')}</legend>
          <label className="mesh-settings__checkbox-row">
            <input
              type="checkbox"
              checked={autopilotEnabled}
              data-testid="ws-feature-autopilot"
              onChange={(event) => setAutopilotEnabled(event.target.checked)}
            />
            <span>{t('workspace.featureFlagAutopilot')}</span>
          </label>
          <p className="mesh-field-hint">{t('workspace.featureFlagsHint')}</p>
        </fieldset>
        {errorKey !== null ? (
          <p role="alert" data-testid="ws-basic-error">
            {t(errorKey, errorValues)}
          </p>
        ) : null}
      </div>

      <DirtyNavigationGuardDialog
        isConfirming={guard.isConfirming}
        title={t('settings.unsavedTitle')}
        description={t('settings.unsavedDescription')}
        stayLabel={t('settings.unsavedStay')}
        discardLabel={t('settings.unsavedDiscard')}
        closeLabel={t('a11y.closeDialog')}
        onStay={guard.stay}
        onDiscard={guard.discard}
      />
    </SettingsSection>
  );
}

interface FormValues {
  name: string;
  slug: string;
  logoUrl: string;
  timezone: string;
  defaultLocale: string;
  defaultTheme: string;
  autopilotEnabled: boolean;
}

/** 由表单值与服务端现状差异构造 PATCH 浅合并载荷(不可变,仅含变更键,W4)。 */
function buildChanges(workspace: WorkspaceDetail, form: FormValues): WorkspacePatch {
  const current = currentSettings(workspace);
  const changes: WorkspacePatch = {};
  if (form.name.trim() !== workspace.name) changes.name = form.name.trim();
  if (form.slug.trim() !== workspace.slug) changes.slug = form.slug.trim();
  if (form.logoUrl.trim() !== (workspace.logo_url ?? '')) {
    changes.logo_url = form.logoUrl.trim() === '' ? null : form.logoUrl.trim();
  }
  if (form.timezone !== workspace.timezone) changes.timezone = form.timezone;
  if (form.defaultLocale !== current.locale) {
    changes.settings = { ...changes.settings, default_locale: form.defaultLocale };
  }
  if (form.defaultTheme !== current.theme) {
    changes.settings = { ...changes.settings, default_theme: form.defaultTheme };
  }
  if (form.autopilotEnabled !== current.autopilot) {
    const existing = workspace.settings.feature_flags ?? {};
    changes.settings = {
      ...changes.settings,
      feature_flags: { ...existing, autopilot: form.autopilotEnabled },
    };
  }
  return changes;
}
