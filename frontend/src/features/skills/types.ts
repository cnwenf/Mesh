/**
 * 技能模块实体类型(skill.md §2/§3 契约,snake_case 与 API JSON 一致)。
 * 四层解耦:定义 → 版本(不可变) → 安装 → 绑定。
 */

export type SkillStatus = 'draft' | 'published' | 'deprecated' | 'disabled';
export type SkillSourceType = 'builtin' | 'user' | 'marketplace' | 'url';
export type SkillTrustLevel = 'trusted' | 'reviewed' | 'untrusted';
export type InstallStatus = 'installed' | 'updated_available' | 'disabled';
export type InstallScope = 'workspace' | 'agent';
export type ImportStatus =
  | 'parsing'
  | 'validating'
  | 'sandbox_preview'
  | 'awaiting_review'
  | 'ready'
  | 'installing'
  | 'installed'
  | 'failed'
  | 'rejected';

export type CapabilityPermission = 'read_only' | 'write' | 'confirm_required';

/** 能力声明:纯字符串 key 或 {capability, permission} 对象(声明层混合格式)。 */
export type CapabilityDeclaration =
  string | { readonly capability: string; readonly permission?: CapabilityPermission };

export interface SkillSummary {
  readonly id: string;
  readonly workspace_id: string;
  readonly source_id: string;
  readonly source_type: SkillSourceType | null;
  readonly trust_level: SkillTrustLevel | null;
  readonly name: string;
  readonly slug: string;
  readonly summary: string;
  readonly status: SkillStatus;
  readonly current_version_id: string | null;
  /** §4.1 card fields (server-supplied; null when not carried). */
  readonly current_version: string | null;
  readonly has_scripts: boolean | null;
  readonly install_status: InstallStatus | null;
  readonly required_capabilities: readonly CapabilityDeclaration[];
  readonly tags: readonly string[];
  readonly icon: string | null;
  readonly created_by: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface SkillDetail extends SkillSummary {
  readonly current_version: string | null;
  readonly has_scripts: boolean;
}

export interface SkillScript {
  readonly id: string;
  readonly path: string;
  readonly runtime: string;
  readonly entrypoint: boolean;
  readonly content_ref: string;
  readonly content_hash: string;
  readonly required_capabilities: readonly CapabilityDeclaration[];
  readonly content?: string;
}

export interface SkillReference {
  readonly id: string;
  readonly path: string;
  readonly media_type: string;
  readonly content_ref: string;
  readonly summary: string | null;
}

export interface SkillTrigger {
  readonly id: string;
  readonly trigger_type: 'keyword' | 'semantic' | 'tag';
  readonly pattern: string;
  readonly weight: number;
}

export interface SkillVersion {
  readonly id: string;
  readonly skill_id: string;
  readonly version: string;
  readonly instructions: string;
  readonly status: 'draft' | 'published' | 'deprecated';
  readonly changelog: string | null;
  readonly io_contract: Record<string, unknown> | null;
  readonly required_capabilities: readonly CapabilityDeclaration[];
  readonly content_hash: string;
  readonly created_by: string;
  readonly created_at: string;
  readonly is_current?: boolean;
  readonly scripts?: readonly SkillScript[];
  readonly references?: readonly SkillReference[];
  readonly triggers?: readonly SkillTrigger[];
}

export interface SkillInstallation {
  readonly id: string;
  readonly workspace_id: string;
  readonly skill_id: string;
  readonly skill_version_id: string;
  readonly scope: InstallScope;
  readonly agent_id: string | null;
  readonly install_status: InstallStatus;
  readonly auto_update: boolean;
  readonly granted_capabilities: readonly CapabilityDeclaration[];
  readonly installed_by: string;
  readonly installed_at: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface AgentSkillBinding {
  readonly id: string;
  readonly agent_id: string;
  readonly skill_id: string;
  readonly skill_installation_id: string;
  readonly skill_version_id: string;
  readonly enabled: boolean;
  readonly auto_trigger: boolean;
  readonly priority: number;
  readonly created_at: string;
  readonly updated_at: string;
}

/** GET /agents/{id}/skills 的联表行(§3.2 示例形状)。 */
export interface AgentSkillRow {
  readonly binding_id: string;
  readonly skill: {
    readonly id: string;
    readonly name: string;
    readonly slug: string;
    readonly summary: string;
    readonly source_type: SkillSourceType | null;
    readonly trust_level: SkillTrustLevel | null;
    readonly status: SkillStatus;
  };
  readonly skill_version_id: string;
  readonly version: string;
  readonly install_status: InstallStatus;
  readonly enabled: boolean;
  readonly auto_trigger: boolean;
  readonly priority: number;
}

export interface ImportPreviewScript {
  readonly path: string;
  readonly runtime: string;
  readonly entrypoint: boolean;
  readonly required_capabilities: readonly CapabilityDeclaration[];
}

export interface ImportPreview {
  readonly name: string;
  readonly version: string;
  readonly summary: string;
  readonly instructions_preview: string;
  readonly scripts: readonly ImportPreviewScript[];
  readonly references: readonly { readonly path: string; readonly media_type: string }[];
  readonly requested_capabilities: readonly string[];
}

export interface ImportTask {
  readonly task_id: string;
  readonly source_type: SkillSourceType;
  readonly uri: string | null;
  readonly ref: string | null;
  readonly status: ImportStatus;
  readonly stage: string;
  readonly percent: number;
  readonly preview: ImportPreview | null;
  readonly requires_approval: boolean;
  readonly skill_id: string | null;
  readonly skill_version_id: string | null;
  readonly installation_id: string | null;
  readonly granted_capabilities: readonly CapabilityDeclaration[];
  readonly error: string | null;
  readonly decision_comment: string | null;
  readonly reviewed_by: string | null;
  readonly reviewed_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface MarketplaceEntry {
  readonly id: string;
  readonly name: string;
  readonly summary: string;
  readonly version: string;
  readonly manifest_url: string;
  readonly downloads: number;
  readonly rating: number;
  readonly certified: boolean;
  readonly has_scripts: boolean;
  readonly tags: readonly string[];
}
