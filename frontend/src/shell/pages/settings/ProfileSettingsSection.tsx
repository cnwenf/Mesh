/**
 * 账号设置 → 个人资料。资料真源为 GET/PATCH /api/v1/users/me；users 模型按
 * auth.md/member.md 只提供 display_name、avatar_url 与 timezone，不虚构 bio 双真源。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiClient } from '../../../api/instance';
import { Avatar, ErrorState, Input, SettingsSection, Skeleton, useToast } from '../../../design';
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

  const load = useCallback(() => {
    let active = true;
    setStatus('loading');
    void fetchMe(client)
      .then((me) => {
        if (!active) return;
        setProfile(me.user);
        setDisplayName(me.user.display_name);
        setAvatarUrl(me.user.avatar_url ?? '');
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
  const notifySaved = (): void => {
    toast.addToast(t('profile.saved'), {
      tone: 'success',
      closeLabel: t('common.close'),
    });
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
    const value = avatarUrl.trim();
    if (!isSecureAvatarUrl(value)) {
      setAvatarError(t('profile.avatarHttps'));
      return;
    }
    setAvatarError(undefined);
    if (value === (currentProfile.avatar_url ?? '')) return;
    const saveVersion = ++avatarSaveVersion.current;
    void updateOwnProfile(client, { avatar_url: value })
      .then((next) => {
        if (saveVersion !== avatarSaveVersion.current) return;
        setProfile((current) =>
          current === null ? next : { ...current, avatar_url: next.avatar_url },
        );
        setAvatarUrl((current) => (current.trim() === value ? (next.avatar_url ?? '') : current));
        notifySaved();
      })
      .catch(() => {
        if (saveVersion === avatarSaveVersion.current) setAvatarError(t('profile.saveError'));
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
            <Avatar name={displayName} src={avatarUrl} size={56} />
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
      </SettingsSection>
    </>
  );
}
