/**
 * 邀请创建面板(workspace.md §4.2/§4.3):邮箱批量 / 链接模式,角色预设,
 * max_uses / expires_in_hours(默认 10 次 / 7 天,受工作区可配置上限约束,§2.3)。
 *
 * 成功:链接模式呈现一次性 invite_link 卡(复制按钮);邮箱模式提示已生成。
 * 422 invitation_limits_exceeded 的 caps(details.max_uses|expires_in_hours + cap)具名呈现;
 * 409 conflict(同邮箱 active 邀请)具名呈现(§3.3)。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:组件与同域纯函数/常量同文件共存 */
import { useState } from 'react';
import type { MeshApiClient } from '../api/client';
import { MeshApiError, errorToI18nKey } from '../api/errors';
import { getApiClient } from '../api/instance';
import { createInvitations } from '../api/invitations';
import type { Invitation, InvitationRole } from '../api/invitations';
import { Button, Input, Select, useToast } from '../design';
import { useT } from '../i18n';
import { EmailChipsInput } from './EmailChipsInput';
import { INVITATION_ROLES } from './permissions';

export interface InvitationCaps {
  maxUsesCap: number;
  lifetimeHoursCap: number;
}

export interface InvitationCreatePanelProps {
  workspaceId: string;
  caps: InvitationCaps;
  onCreated(): void;
  client?: MeshApiClient;
}

type CreateMode = 'link' | 'email';

/** 完整邀请 URL(invite_link 为站内路径,拼接当前 origin) */
export function fullInviteUrl(inviteLink: string): string {
  return `${window.location.origin}${inviteLink}`;
}

export function InvitationCreatePanel(props: InvitationCreatePanelProps): React.JSX.Element {
  const { workspaceId, caps, onCreated } = props;
  const client = props.client ?? getApiClient();
  const t = useT();
  const { addToast } = useToast();

  const [mode, setMode] = useState<CreateMode>('link');
  const [emails, setEmails] = useState<string[]>([]);
  const [role, setRole] = useState<InvitationRole>('member');
  const [maxUses, setMaxUses] = useState('');
  const [expiresInHours, setExpiresInHours] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [errorValues, setErrorValues] = useState<Record<string, unknown>>({});
  const [lastLinks, setLastLinks] = useState<readonly Invitation[]>([]);
  const [copied, setCopied] = useState<string | null>(null);

  const handleSubmit = async (): Promise<void> => {
    setIsSubmitting(true);
    setErrorKey(null);
    const input: Parameters<typeof createInvitations>[2] = { role };
    if (mode === 'email' && emails.length > 0) {
      input.emails = emails;
    }
    if (maxUses.trim() !== '') {
      input.max_uses = Number.parseInt(maxUses, 10);
    }
    if (expiresInHours.trim() !== '') {
      input.expires_in_hours = Number.parseInt(expiresInHours, 10);
    }
    try {
      const created = await createInvitations(client, workspaceId, input);
      setLastLinks(created.filter((invitation) => invitation.invite_link !== undefined));
      setEmails([]);
      setMaxUses('');
      setExpiresInHours('');
      addToast(t('invitations.createdToast'), {
        tone: 'success',
        closeLabel: t('a11y.dismiss'),
      });
      onCreated();
    } catch (err) {
      if (err instanceof MeshApiError && err.code === 'invitation_limits_exceeded') {
        const details = err.details ?? {};
        if ('max_uses' in details) {
          setErrorKey('invitations.limitsUses');
          setErrorValues({ value: details.max_uses, cap: details.cap });
        } else {
          setErrorKey('invitations.limitsHours');
          setErrorValues({ value: details.expires_in_hours, cap: details.cap });
        }
      } else if (err instanceof MeshApiError && err.code === 'conflict') {
        setErrorKey('invitations.conflictEmail');
        setErrorValues({ email: (err.details ?? {}).email ?? '' });
      } else {
        setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
        setErrorValues({});
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const copyLink = async (invitation: Invitation): Promise<void> => {
    const link = invitation.invite_link;
    if (link === undefined) return;
    const url = fullInviteUrl(link);
    try {
      await navigator.clipboard.writeText(url);
      setCopied(invitation.id);
      addToast(t('invitations.copiedToast'), {
        tone: 'success',
        closeLabel: t('a11y.dismiss'),
      });
    } catch {
      addToast(t('invitations.copyFailedToast'), {
        tone: 'warn',
        closeLabel: t('a11y.dismiss'),
      });
    }
  };

  return (
    <div className="mesh-invite-create" data-testid="invitation-create">
      <div role="radiogroup" aria-label={t('invitations.modeLabel')}>
        <label>
          <input
            type="radio"
            name="invite-mode"
            data-testid="invite-mode-link"
            checked={mode === 'link'}
            onChange={() => setMode('link')}
          />
          {t('invitations.modeLink')}
        </label>
        <label>
          <input
            type="radio"
            name="invite-mode"
            data-testid="invite-mode-email"
            checked={mode === 'email'}
            onChange={() => setMode('email')}
          />
          {t('invitations.modeEmail')}
        </label>
      </div>

      {mode === 'email' ? (
        <EmailChipsInput
          label={t('invitations.emailsLabel')}
          emails={emails}
          onChange={setEmails}
          placeholder={t('invitations.emailsPlaceholder')}
          invalidFormatHint={t('wsCreate.inviteInvalid')}
          maxCountHint={t('wsCreate.inviteTooMany')}
          removeLabel={t('wsCreate.inviteRemove')}
        />
      ) : null}

      <Select
        label={t('invitations.roleLabel')}
        data-testid="invite-role"
        value={role}
        onChange={(event) => setRole(event.target.value as InvitationRole)}
      >
        {INVITATION_ROLES.map((candidate) => (
          <option key={candidate} value={candidate}>
            {t(`roles.${candidate}`)}
          </option>
        ))}
      </Select>

      <Input
        label={t('invitations.maxUsesLabel')}
        hint={t('invitations.maxUsesHint', { cap: caps.maxUsesCap })}
        type="number"
        min={1}
        max={caps.maxUsesCap}
        data-testid="invite-max-uses"
        value={maxUses}
        onChange={(event) => setMaxUses(event.target.value)}
      />
      <Input
        label={t('invitations.expiresLabel')}
        hint={t('invitations.expiresHint', { cap: caps.lifetimeHoursCap })}
        type="number"
        min={1}
        max={caps.lifetimeHoursCap}
        data-testid="invite-expires-hours"
        value={expiresInHours}
        onChange={(event) => setExpiresInHours(event.target.value)}
      />

      {errorKey !== null ? (
        <p role="alert" data-testid="invite-create-error">
          {t(errorKey, errorValues)}
        </p>
      ) : null}

      <Button
        data-testid="invite-submit"
        isLoading={isSubmitting}
        onClick={() => void handleSubmit()}
      >
        {mode === 'email' ? t('invitations.sendEmails') : t('invitations.generateLink')}
      </Button>

      {lastLinks.length > 0 ? (
        <div className="mesh-invite-links" data-testid="invite-links">
          <h4>{t('invitations.linkCardTitle')}</h4>
          <p className="mesh-invite-links__once">{t('invitations.linkOnceHint')}</p>
          <ul>
            {lastLinks.map((invitation) => (
              <li key={invitation.id} data-testid="invite-link-row">
                <code data-testid="invite-link-url">
                  {invitation.invite_link !== undefined
                    ? fullInviteUrl(invitation.invite_link)
                    : ''}
                </code>
                <Button
                  size="sm"
                  variant="secondary"
                  data-testid="invite-copy"
                  onClick={() => void copyLink(invitation)}
                >
                  {copied === invitation.id ? t('invitations.copied') : t('invitations.copy')}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
