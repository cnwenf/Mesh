/**
 * 成员头像墙(squad.md §4.1 / §4.3-1):把 member_preview 渲染为头像列。
 * 头像取显示名首字;leader 以 (L) 角标标注;人 / agent 以不同图标区分
 * (README §6.12:颜色非唯一信号,图标 + 文本标题双通道)。
 */
import { useT } from '../../i18n';
import type { MemberPreview } from './types';

/** 显示名首字(空名回退 '?');取首个码点以兼容多字节字符。 */
function initialsOf(name: string): string {
  const trimmed = name.trim();
  if (trimmed === '') return '?';
  return Array.from(trimmed)[0].toUpperCase();
}

function HumanGlyph(): React.JSX.Element {
  return (
    <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true" focusable="false">
      <circle cx="8" cy="5" r="3" fill="currentColor" />
      <path d="M2 14c0-3 2.7-5 6-5s6 2 6 5" fill="currentColor" />
    </svg>
  );
}

function AgentGlyph(): React.JSX.Element {
  return (
    <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true" focusable="false">
      <rect x="3" y="4" width="10" height="8" rx="1.5" fill="currentColor" />
      <rect x="6" y="6.5" width="1.4" height="1.4" fill="var(--color-surface)" />
      <rect x="8.8" y="6.5" width="1.4" height="1.4" fill="var(--color-surface)" />
      <rect x="7.3" y="1" width="1.4" height="3" fill="currentColor" />
    </svg>
  );
}

export interface MemberAvatarWallProps {
  readonly members: readonly MemberPreview[] | undefined;
  /** 头像上限(member_preview 服务端已截断至 8;此处仅防御)。 */
  readonly limit?: number;
}

export function MemberAvatarWall(props: MemberAvatarWallProps): React.JSX.Element {
  const t = useT();
  const { members, limit = 8 } = props;
  // 防御:旧响应 / 畸形信封可能缺 member_preview,回退空墙而非渲染期抛错。
  const visible = (members ?? []).slice(0, limit);

  if (visible.length === 0) {
    return <p className="mesh-squads__avatarwall-empty">{t('squads.detail.noMembers')}</p>;
  }

  return (
    <ul className="mesh-squads__avatarwall" data-testid="squad-avatarwall">
      {visible.map((member) => {
        const isLeader = member.role === 'leader';
        const typeLabel =
          member.member_type === 'agent' ? t('squads.agentBadge') : t('squads.humanBadge');
        const title = isLeader
          ? `${member.name} · ${typeLabel} · ${t('squads.role.leader')}`
          : `${member.name} · ${typeLabel}`;
        return (
          <li
            key={member.member_id}
            className={
              member.member_type === 'agent'
                ? 'mesh-squads__avatar mesh-squads__avatar--agent'
                : 'mesh-squads__avatar mesh-squads__avatar--human'
            }
            title={title}
            data-testid={`squad-avatar-${member.member_id}`}
          >
            <span className="mesh-squads__avatar-initial">{initialsOf(member.name)}</span>
            <span className="mesh-squads__avatar-type">
              {member.member_type === 'agent' ? <AgentGlyph /> : <HumanGlyph />}
            </span>
            {isLeader ? (
              <span
                className="mesh-squads__avatar-leader"
                data-testid={`squad-avatar-leader-${member.member_id}`}
              >
                {t('squads.avatar.leaderMark')}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
