/**
 * 账号设置 → 个人资料。资料真源为 GET/PATCH /api/v1/users/me；users 模型按
 * auth.md/member.md 只提供 display_name、avatar_url 与 timezone，不虚构 bio 双真源。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { MeshApiError } from '../../../api';
import { getApiClient } from '../../../api/instance';
import {
  Avatar,
  Button,
  ErrorState,
  Input,
  SettingsSection,
  Skeleton,
  useToast,
} from '../../../design';
import { fetchMe, updateOwnProfile } from '../../../features/members/api';
import type { MeResponse } from '../../../features/members/types';
import { useT } from '../../../i18n';
import { isSecureAvatarUrl } from './profileValidation';

type UserProfile = MeResponse['user'];

export function ProfileSettingsSection(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = getApiClient();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [avatarUrl, setAvatarUrl] = useState('');
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [nameError, setNameError] = useState<string | undefined>();
  const [avatarError, setAvatarError] = useState<string | undefined>();
  const nameSaveVersion = useRef(0);
  const avatarSaveVersion = useRef(0);
  const skipNextAvatarBlurSave = useRef(false);
  const avatarWriteQueue = useRef<Promise<void>>(Promise.resolve());
  const authoritativeAvatarUrl = useRef<string | null>(null);

  const load = useCallback(() => {
    let active = true;
    setStatus('loading');
    void fetchMe(client)
      .then((me) => {
        if (!active) return;
        setProfile(me.user);
        setDisplayName(me.user.display_name);
        setAvatarUrl(me.user.avatar_url ?? '');
        authoritativeAvatarUrl.current = me.user.avatar_url ?? null;
        setStatus('ready');
      })
      .catch(() => {
        if (active) setStatus('error');
      });
    return () => {
      active = false;
    };
  }, [client]);

  useEffect(() => load(), [load]);

  if (status === 'loading') {
    return <Skeleton className="mesh-settings__skeleton" loadingLabel={t('common.loading')} />;
  }
  if (status === 'error' || profile === null) {
    return (
      <ErrorState
        title={t('state.errorTitle')}
        description={t('state.errorDescription')}
        retryLabel={t('common.retry')}
        onRetry={load}
      />
    );
  }
  const currentProfile = profile;
  const trimmedAvatarUrl = avatarUrl.trim();
  const previewAvatarUrl = isSecureAvatarUrl(trimmedAvatarUrl) ? trimmedAvatarUrl : undefined;
  const notifySaved = (): void => {
    toast.addToast(t('profile.saved'), {
      tone: 'success',
      closeLabel: t('common.close'),
    });
  };
  const avatarSaveError = (error: unknown): string =>
    error instanceof MeshApiError && error.code === 'validation_error'
      ? t('profile.avatarHttps')
      : t('profile.saveError');
  const enqueueAvatarUpdate = (avatar_url: string | null): Promise<UserProfile> => {
    const request = avatarWriteQueue.current.then(async () => {
      const next = await updateOwnProfile(client, { avatar_url });
      authoritativeAvatarUrl.current = next.avatar_url ?? null;
      return next;
    });
    // Keep the tail fulfilled so a rejected save never blocks the next user
    // action. Serial order makes the final server value match interaction order.
    avatarWriteQueue.current = request.then(
      () => undefined,
      () => undefined,
    );
    return request;
  };

  const saveDisplayName = (): void => {
    const value = displayName.trim();
    if (value.length === 0 || value.length > 80) {
      setNameError(t('profile.nameError'));
      return;
    }
    setNameError(undefined);
    if (value === currentProfile.display_name) return;
    const saveVersion = ++nameSaveVersion.current;
    void updateOwnProfile(client, { display_name: value })
      .then((next) => {
        if (saveVersion !== nameSaveVersion.current) return;
        setProfile((current) =>
          current === null ? next : { ...current, display_name: next.display_name },
        );
        setDisplayName((current) => (current.trim() === value ? next.display_name : current));
        notifySaved();
      })
      .catch(() => {
        if (saveVersion === nameSaveVersion.current) setNameError(t('profile.saveError'));
      });
  };

  const saveAvatarUrl = (): void => {
    // Pointer activation of "restore default" blurs the URL input before the
    // button click. Suppress that one blur save so a draft URL and the explicit
    // `avatar_url:null` clear are never issued concurrently.
    if (skipNextAvatarBlurSave.current) {
      skipNextAvatarBlurSave.current = false;
      return;
    }
    const value = avatarUrl.trim();
    if (!isSecureAvatarUrl(value)) {
      setAvatarError(t('profile.avatarHttps'));
      return;
    }
    setAvatarError(undefined);
    if (value === (currentProfile.avatar_url ?? '')) return;
    const saveVersion = ++avatarSaveVersion.current;
    void enqueueAvatarUpdate(value)
      .then((next) => {
        if (saveVersion !== avatarSaveVersion.current) return;
        setProfile((current) =>
          current === null ? next : { ...current, avatar_url: next.avatar_url },
        );
        setAvatarUrl((current) => (current.trim() === value ? (next.avatar_url ?? '') : current));
        notifySaved();
      })
      .catch((error: unknown) => {
        if (saveVersion === avatarSaveVersion.current) setAvatarError(avatarSaveError(error));
      });
  };

  const clearAvatar = (): void => {
    skipNextAvatarBlurSave.current = false;
    if ((currentProfile.avatar_url ?? '') === '' && avatarUrl === '') return;
    const saveVersion = ++avatarSaveVersion.current;
    setAvatarError(undefined);
    setProfile((current) => (current === null ? current : { ...current, avatar_url: null }));
    setAvatarUrl('');
    void enqueueAvatarUpdate(null)
      .then((next) => {
        if (saveVersion !== avatarSaveVersion.current) return;
        setProfile((current) =>
          current === null ? next : { ...current, avatar_url: next.avatar_url },
        );
        setAvatarUrl((current) => (current === '' ? (next.avatar_url ?? '') : current));
        notifySaved();
      })
      .catch((error: unknown) => {
        if (saveVersion !== avatarSaveVersion.current) return;
        const rollbackAvatar = authoritativeAvatarUrl.current;
        setProfile((current) =>
          current === null ? current : { ...current, avatar_url: rollbackAvatar },
        );
        setAvatarUrl((current) => (current === '' ? (rollbackAvatar ?? '') : current));
        setAvatarError(avatarSaveError(error));
      });
  };

  return (
    <>
      <h2 className="mesh-settings-content-title">{t('profile.title')}</h2>
      <SettingsSection title={t('profile.personalInformation')} layout="rows">
        <div className="mesh-settings-section__identity-row">
          <div className="mesh-settings-section__identity-copy">
            <strong>{t('profile.avatar')}</strong>
            <span>{currentProfile.email}</span>
          </div>
          <div className="mesh-settings-section__identity-preview">
            <Avatar name={displayName} src={previewAvatarUrl} size={56} />
          </div>
        </div>
        <Input
          label={t('profile.name')}
          value={displayName}
          maxLength={80}
          error={nameError}
          onChange={(event) => setDisplayName(event.target.value)}
          onBlur={saveDisplayName}
        />
        <Input
          label={t('profile.avatarUrl')}
          value={avatarUrl}
          inputMode="url"
          error={avatarError}
          hint={t('profile.avatarHint')}
          onChange={(event) => setAvatarUrl(event.target.value)}
          onBlur={saveAvatarUrl}
        />
        {avatarUrl !== '' || currentProfile.avatar_url ? (
          <Button
            type="button"
            variant="secondary"
            onPointerDown={() => {
              skipNextAvatarBlurSave.current = true;
            }}
            onClick={clearAvatar}
          >
            {t('profile.restoreDefaultAvatar')}
          </Button>
        ) : null}
      </SettingsSection>
    </>
  );
}
