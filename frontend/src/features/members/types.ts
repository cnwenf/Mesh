/**
 * 成员名册实体类型(member.md §2.2 / §3.2)。
 * `members.id` 为全系统统一引用键;`display_name` 为服务端按 §2.4 解析后的单一显示名。
 */

export type MemberType = 'human' | 'agent';
export type MemberRole = 'owner' | 'admin' | 'member' | 'guest';
export type MemberStatus = 'active' | 'disabled' | 'removed';

export interface HumanProfile {
  readonly id: string;
  readonly full_name: string;
  readonly email: string;
  readonly avatar_url: string | null;
}

export interface AgentProfile {
  readonly id: string;
  readonly name: string | null;
  readonly description: string | null;
  readonly avatar_url: string | null;
  readonly is_active: boolean | null;
  readonly role_tag?: string | null;
  readonly lifecycle_status?: string | null;
}

export interface MemberSummary {
  readonly id: string;
  readonly member_type: MemberType;
  readonly role: MemberRole;
  readonly status: MemberStatus;
  readonly display_name: string;
  readonly joined_at: string | null;
  readonly profile: HumanProfile | AgentProfile | null;
}

export interface MemberDetail extends MemberSummary {
  readonly display_override: string | null;
  readonly disabled_at: string | null;
  readonly counts: { readonly open_issues_assigned: number };
}

export interface Membership {
  readonly workspace_id: string;
  readonly workspace_name: string;
  readonly workspace_slug: string;
  readonly role: MemberRole;
  readonly status: MemberStatus;
  readonly joined_at: string | null;
}

export interface MeResponse {
  readonly user: {
    readonly id: string;
    readonly email: string;
    readonly display_name: string;
    readonly avatar_url?: string | null;
    readonly timezone?: string | null;
    /** active workspace 解析序 ③ 的服务端提示(search-command-palette.md §3.4)。 */
    readonly last_active_workspace_id?: string | null;
  };
  readonly memberships: readonly Membership[];
}

export interface ProjectAccess {
  readonly id: string;
  readonly member_id: string;
  readonly project_id: string;
  readonly permission: 'read' | 'write';
}

/** 名册列表筛选(与 URL 查询参数同源,§6.12 单一页面投影)。 */
export interface RosterFilters {
  readonly memberType: 'all' | MemberType;
  readonly status: 'default' | 'all' | MemberStatus;
  readonly q: string;
}

export const ROLE_ORDER: readonly MemberRole[] = ['owner', 'admin', 'member', 'guest'];
