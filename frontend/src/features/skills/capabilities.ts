import type { CapabilityDeclaration, CapabilityGrant, CapabilityPermission } from './types';

export interface EffectiveCapability {
  readonly capability: string;
  readonly permission: CapabilityPermission;
}

const PERMISSION_STRICTNESS: Readonly<Record<CapabilityPermission, number>> = {
  read_only: 1,
  write: 2,
  confirm_required: 3,
};

/**
 * Declaration-layer shorthand uses the security-safe default from skill.md §2.4:
 * a bare capability key always requires human confirmation.
 */
export function normalizeCapability(declaration: CapabilityDeclaration): EffectiveCapability {
  return typeof declaration === 'string'
    ? { capability: declaration, permission: 'confirm_required' }
    : {
        capability: declaration.capability,
        permission: declaration.permission ?? 'confirm_required',
      };
}

/**
 * Mirrors the enqueue normalizer: de-duplicate keys, keep the stricter grant,
 * and sort for a stable, auditable presentation.
 */
export function effectiveCapabilities(
  declarations: readonly CapabilityDeclaration[],
): readonly EffectiveCapability[] {
  const byCapability = new Map<string, EffectiveCapability>();
  for (const declaration of declarations) {
    const normalized = normalizeCapability(declaration);
    const current = byCapability.get(normalized.capability);
    if (
      current === undefined ||
      PERMISSION_STRICTNESS[normalized.permission] > PERMISSION_STRICTNESS[current.permission]
    ) {
      byCapability.set(normalized.capability, normalized);
    }
  }
  return [...byCapability.values()].sort((left, right) =>
    left.capability.localeCompare(right.capability),
  );
}

/** Installation grants add runtime enabled state on top of declaration fields. */
export function effectiveGrants(
  grants: readonly CapabilityGrant[],
): readonly EffectiveCapability[] {
  return effectiveCapabilities(
    grants.filter((grant) => typeof grant === 'string' || grant.enabled !== false),
  );
}

export type CapabilityTone = 'neutral' | 'danger' | 'warning';

/** Text remains visible beside the tone; color is never the only signal. */
export function permissionTone(permission: CapabilityPermission): CapabilityTone {
  if (permission === 'write') return 'danger';
  if (permission === 'confirm_required') return 'warning';
  return 'neutral';
}
