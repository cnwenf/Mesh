import { useT } from '../../i18n';
import { effectiveCapabilities, permissionTone } from './capabilities';
import type { CapabilityDeclaration } from './types';

/** Capability key + explicit permission text; color is supplementary only. */
export function CapabilitySignals({
  declarations,
  className = '',
}: {
  readonly declarations: readonly CapabilityDeclaration[];
  readonly className?: string;
}): React.JSX.Element | null {
  const t = useT();
  const capabilities = effectiveCapabilities(declarations);
  if (capabilities.length === 0) return null;

  return (
    <ul
      className={`mesh-skills__capabilities ${className}`.trim()}
      aria-label={t('skills.sideRequiredCaps')}
    >
      {capabilities.map((capability) => (
        <li
          key={capability.capability}
          className={`mesh-skills__capability mesh-skills__capability--${permissionTone(capability.permission)}`}
        >
          <code>{capability.capability}</code>
          <span>{t(`skills.permission.${capability.permission}`)}</span>
        </li>
      ))}
    </ul>
  );
}
