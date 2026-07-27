/**
 * Agent 实体类型(agent.md §2.3 / §3.2)。
 * `agents.id` 为 agent 身份键;名册行经 members.agent_id → agents.id 关联(README §6.1)。
 * 时间一律 UTC RFC3339 字符串;id 一律 UUID 字符串。
 */

export type AgentLifecycleStatus = 'active' | 'paused' | 'disabled' | 'archived';
export type AgentVisibility = 'workspace' | 'private';
export type ModelTier = 'strong_reasoning' | 'balanced' | 'lightweight_fast';
export type ReasoningEffort = 'low' | 'medium' | 'high';

export interface AgentModelConfig {
  readonly model?: string;
  readonly model_tier?: ModelTier;
  readonly temperature?: number;
  readonly top_p?: number;
  readonly max_tokens?: number;
  readonly reasoning_effort?: ReasoningEffort;
  readonly stop_sequences?: readonly string[];
  readonly preset?: string;
  readonly advanced?: Record<string, unknown>;
}

export interface AgentMemberRef {
  readonly id: string;
  readonly member_type: 'agent';
  readonly display_name: string;
  readonly avatar_url: string | null;
  readonly role_tag: string | null;
  readonly role: string;
  readonly status: string;
}

export interface AgentSummary {
  readonly id: string;
  readonly member: AgentMemberRef | null;
  readonly display_name: string;
  readonly name: string;
  readonly avatar_url: string | null;
  readonly role_tag: string | null;
  readonly badge_kind: string;
  readonly lifecycle_status: AgentLifecycleStatus;
  readonly visibility: AgentVisibility;
  readonly trigger_on_assign: boolean;
  readonly owner_user_id: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface AgentConfigVersionRef {
  readonly id: string;
  readonly change_summary: string | null;
  readonly changed_by: string;
  readonly created_at: string;
}

export interface AgentDetail extends AgentSummary {
  readonly slug: string | null;
  readonly bio: string | null;
  readonly system_instructions: string | null;
  readonly model_config: AgentModelConfig;
  readonly default_runtime_id: string | null;
  readonly active_config_version_id: string | null;
  readonly current_version: AgentConfigVersionRef | null;
}

export interface AgentConfigVersion {
  readonly id: string;
  readonly agent_id: string;
  readonly snapshot: {
    readonly system_instructions?: string | null;
    readonly model_config?: AgentModelConfig;
    readonly skill_versions?: Record<string, string>;
    readonly capability_grants?: readonly unknown[];
  };
  readonly change_summary: string | null;
  readonly changed_by: string;
  readonly created_at: string;
}

export const MODEL_TIER_ORDER: readonly ModelTier[] = [
  'strong_reasoning',
  'balanced',
  'lightweight_fast',
];

/**
 * 平台模型注册表(agent.md §2.4):`model` 由平台枚举、不暴露具体供应商。
 * 详情页/向导的「具体模型」下拉与此同源(§4.3 线框「具体模型 ▾」)。
 */
export interface PlatformModel {
  readonly value: string;
  readonly tier: ModelTier;
  readonly labelKey: string;
}

export const PLATFORM_MODELS: readonly PlatformModel[] = [
  { value: 'mainstream-llm-strong', tier: 'strong_reasoning', labelKey: 'agents.model.strong' },
  { value: 'mainstream-llm-balanced', tier: 'balanced', labelKey: 'agents.model.balanced' },
  { value: 'mainstream-llm-light', tier: 'lightweight_fast', labelKey: 'agents.model.light' },
];

export const REASONING_EFFORT_ORDER: readonly ReasoningEffort[] = ['low', 'medium', 'high'];

export const AGENT_LIFECYCLE_ORDER: readonly AgentLifecycleStatus[] = [
  'active',
  'paused',
  'disabled',
  'archived',
];
