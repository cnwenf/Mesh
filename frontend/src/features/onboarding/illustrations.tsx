/**
 * 上手引导 / 空状态插画(onboarding.md §1.2.2/§4.2)。
 * 纯内联 SVG 线稿:描边/填色一律经语义 token(var(--color-*))随亮/暗主题适配,
 * 绝不硬编码色值(README §6.12 主题契约)。装饰性节点 aria-hidden + role presentation。
 */

const ILLUSTRATION_WIDTH = 120;
const ILLUSTRATION_HEIGHT = 88;

interface SvgProps {
  readonly stroke: string;
  readonly children: React.ReactNode;
  readonly testId: string;
}

function Illustration({ stroke, children, testId }: SvgProps): React.JSX.Element {
  return (
    <svg
      width={ILLUSTRATION_WIDTH}
      height={ILLUSTRATION_HEIGHT}
      viewBox="0 0 120 88"
      fill="none"
      stroke={stroke}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      role="presentation"
      data-testid={testId}
    >
      {children}
    </svg>
  );
}

/** 空收件托盘(收件箱空态) */
export function EmptyInboxTray(): React.JSX.Element {
  return (
    <Illustration stroke="var(--color-border)" testId="illustration-inbox-tray">
      <path d="M18 30 L60 12 L102 30 L102 62 L18 62 Z" />
      <path d="M18 48 L42 48 L48 56 L72 56 L78 48 L102 48" stroke="var(--color-info)" />
      <circle cx="60" cy="30" r="3" stroke="var(--color-info)" />
    </Illustration>
  );
}

/** 空文件夹(项目空态) */
export function EmptyFolder(): React.JSX.Element {
  return (
    <Illustration stroke="var(--color-border)" testId="illustration-folder">
      <path d="M16 24 L44 24 L52 32 L104 32 L104 68 L16 68 Z" />
      <path d="M28 48 L72 48" stroke="var(--color-info)" />
      <path d="M28 56 L58 56" stroke="var(--color-info)" />
    </Illustration>
  );
}

/** 空看板列(看板空态) */
export function EmptyBoardColumns(): React.JSX.Element {
  return (
    <Illustration stroke="var(--color-border)" testId="illustration-board">
      <rect x="16" y="16" width="26" height="56" rx="3" />
      <rect x="47" y="16" width="26" height="56" rx="3" />
      <rect x="78" y="16" width="26" height="56" rx="3" />
      <rect x="21" y="24" width="16" height="10" rx="2" stroke="var(--color-info)" />
      <path d="M55 44 L65 44" stroke="var(--color-info)" />
    </Illustration>
  );
}

/** 空名册(成员空态) */
export function EmptyRoster(): React.JSX.Element {
  return (
    <Illustration stroke="var(--color-border)" testId="illustration-roster">
      <circle cx="44" cy="32" r="10" />
      <path d="M26 66 C26 52 62 52 62 66" />
      <circle cx="78" cy="36" r="7" stroke="var(--color-info)" />
      <path d="M68 64 C68 54 92 54 92 64" stroke="var(--color-info)" />
    </Illustration>
  );
}

/** 空会话气泡(聊天空态) */
export function EmptyChatBubbles(): React.JSX.Element {
  return (
    <Illustration stroke="var(--color-border)" testId="illustration-chat">
      <path d="M18 20 L74 20 L74 48 L40 48 L30 58 L30 48 L18 48 Z" />
      <path d="M30 30 L60 30" stroke="var(--color-info)" />
      <path d="M30 38 L50 38" stroke="var(--color-info)" />
      <path d="M84 36 L102 36 L102 58 L94 58 L94 66 L86 58 L84 58 Z" stroke="var(--color-info)" />
    </Illustration>
  );
}

/** 空自动化(autopilot 空态) */
export function EmptyAutomation(): React.JSX.Element {
  return (
    <Illustration stroke="var(--color-border)" testId="illustration-automation">
      <circle cx="60" cy="44" r="14" />
      <path d="M60 30 L60 22 M60 66 L60 58 M46 44 L38 44 M82 44 L74 44" />
      <path d="M50 34 L44 28 M76 60 L70 54 M70 34 L76 28 M44 60 L50 54" stroke="var(--color-info)" />
      <circle cx="60" cy="44" r="3" stroke="var(--color-info)" />
    </Illustration>
  );
}

/** aha 庆祝(末步达成:第一位 AI 队友上岗) */
export function AhaCelebration(): React.JSX.Element {
  return (
    <Illustration stroke="var(--color-success)" testId="illustration-aha">
      <circle cx="60" cy="44" r="24" />
      <path d="M50 44 L57 52 L72 36" />
      <path d="M28 18 L24 10 M92 18 L96 10 M18 44 L10 44 M110 44 L102 44" stroke="var(--color-info)" />
    </Illustration>
  );
}
