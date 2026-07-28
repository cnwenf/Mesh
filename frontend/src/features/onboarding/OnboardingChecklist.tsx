/**
 * 上手清单卡片(onboarding.md §4.1/§4.2):进度条 + 五步清单(逐步勾选态 + 每步 CTA
 * 深链既有向导 + 自动完成来源角标)+ 整体关闭;末步达成切换为 aha 庆祝卡片(可收起)。
 *
 * - 首个未完成步骤高亮并默认展开 CTA;完成态叠加 ✓ 图标 + 文字标签(颜色不作唯一信号);
 * - 隐藏条件:加载中 / 无状态 / 已 dismiss;庆祝态尊重 prefers-reduced-motion(动画非唯一信号);
 * - 进度真源在数据库(§3.7):CTA 仅深链,步骤推进经领域事件 + 实时帧重拉。
 */
import { useNavigate } from 'react-router';
import { Button } from '../../design';
import { useT } from '../../i18n';
import { AhaCelebration } from './illustrations';
import type { OnboardingState, OnboardingStep, OnboardingStepKey } from './types';
import { STEP_KEYS } from './types';
import { useOnboarding } from './useOnboarding';
import './onboarding.css';

const PERCENT_BASE = 100;

interface StepDeeplinkProps {
  readonly workspaceSlug: string | null;
}

/** 激活路径五步 CTA 深链(§1.2.1 深链既有向导目录,一律落地既有页面,不另建向导)。 */
function stepDeeplink(stepKey: OnboardingStepKey, props: StepDeeplinkProps): string {
  switch (stepKey) {
    case 'create_workspace':
      return props.workspaceSlug !== null ? `/w/${props.workspaceSlug}/settings` : '/settings';
    case 'invite_member_or_add_agent':
      return '/members';
    case 'create_first_issue':
      return '/board';
    case 'dispatch_or_mention_agent':
      return '/board';
    case 'see_agent_reply_in_inbox':
      return '/inbox';
  }
}

interface StepRowProps {
  readonly step: OnboardingStep;
  readonly highlighted: boolean;
  readonly deeplink: string;
}

function StepRow(props: StepRowProps): React.JSX.Element {
  const t = useT();
  const navigate = useNavigate();
  const { step, highlighted, deeplink } = props;
  const completed = step.status === 'completed';
  const rowClass = highlighted
    ? 'mesh-onboarding__step mesh-onboarding__step--current'
    : 'mesh-onboarding__step';
  return (
    <li className={rowClass} data-testid={`onboarding-step-${step.step_key}`}>
      <span className="mesh-onboarding__step-line">
        {completed ? (
          <span
            className="mesh-onboarding__check mesh-onboarding__check--done"
            aria-label={t('onboarding.completedBadge')}
            data-testid={`onboarding-check-${step.step_key}`}
          >
            ✓
          </span>
        ) : (
          <span className="mesh-onboarding__check" aria-hidden="true" />
        )}
        <span className="mesh-onboarding__step-name">{t(`onboarding.step.${step.step_key}.name`)}</span>
        {completed && step.completed_via === 'auto' ? (
          <span className="mesh-onboarding__badge" data-testid={`onboarding-auto-badge-${step.step_key}`}>
            {t('onboarding.autoBadge')}
          </span>
        ) : null}
      </span>
      <span className="mesh-onboarding__step-detail">
        <span className="mesh-onboarding__step-description">
          {t(`onboarding.step.${step.step_key}.description`)}
        </span>
        <Button
          size="sm"
          variant={highlighted ? 'primary' : 'secondary'}
          data-testid={`onboarding-cta-${step.step_key}`}
          onClick={() => navigate(deeplink)}
        >
          {t(`onboarding.step.${step.step_key}.cta`)}
        </Button>
      </span>
    </li>
  );
}

interface ChecklistCardProps {
  readonly state: OnboardingState;
  readonly workspaceSlug: string | null;
  readonly onDismiss: () => void;
}

function ChecklistCard(props: ChecklistCardProps): React.JSX.Element {
  const t = useT();
  const { state, workspaceSlug, onDismiss } = props;
  const { completed, total } = state.progress;
  const percent = total === 0 ? 0 : Math.round((completed / total) * PERCENT_BASE);
  const stepsByKey = new Map(state.steps.map((step) => [step.step_key, step]));
  const firstPendingKey =
    STEP_KEYS.find((key) => stepsByKey.get(key)?.status === 'pending') ?? null;

  return (
    <section className="mesh-onboarding" data-testid="onboarding-card" aria-label={t('onboarding.title')}>
      <header className="mesh-onboarding__head">
        <h2 className="mesh-onboarding__title">{t('onboarding.title')}</h2>
        <button
          type="button"
          className="mesh-onboarding__dismiss"
          data-testid="onboarding-dismiss"
          onClick={onDismiss}
        >
          {t('onboarding.dismiss')}
        </button>
      </header>
      <div
        className="mesh-onboarding__progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={completed}
        aria-label={t('onboarding.progressLabel', { completed, total })}
        data-testid="onboarding-progress"
      >
        <span className="mesh-onboarding__progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <p className="mesh-onboarding__progress-text">
        {t('onboarding.progressLabel', { completed, total })}
        {' · '}
        {t('onboarding.percentLabel', { percent })}
      </p>
      <ul className="mesh-onboarding__steps">
        {STEP_KEYS.map((key) => {
          const step = stepsByKey.get(key);
          if (step === undefined) return null;
          return (
            <StepRow
              key={key}
              step={step}
              highlighted={key === firstPendingKey}
              deeplink={stepDeeplink(key, { workspaceSlug })}
            />
          );
        })}
      </ul>
    </section>
  );
}

interface AhaCardProps {
  readonly onDismiss: () => void;
  readonly onOpenInbox: () => void;
}

function AhaCard(props: AhaCardProps): React.JSX.Element {
  const t = useT();
  return (
    <section className="mesh-onboarding mesh-onboarding--aha" data-testid="onboarding-aha-card" aria-label={t('onboarding.aha.title')}>
      <AhaCelebration />
      <h2 className="mesh-onboarding__aha-title">{t('onboarding.aha.title')}</h2>
      <p className="mesh-onboarding__aha-description">{t('onboarding.aha.description')}</p>
      <div className="mesh-onboarding__aha-actions">
        <Button data-testid="onboarding-aha-action" onClick={props.onOpenInbox}>
          {t('onboarding.aha.action')}
        </Button>
        <Button variant="ghost" data-testid="onboarding-aha-close" onClick={props.onDismiss}>
          {t('onboarding.aha.close')}
        </Button>
      </div>
    </section>
  );
}

/**
 * 常驻卡片外壳(挂载于 AppShell main 顶部,onboarding.md §4.1「任意核心页面顶部」)。
 * 自隐藏:加载中 / 无清单 / 已 dismiss;aha 达成后转庆祝卡片(收起即 dismiss)。
 */
export function OnboardingChecklist(): React.JSX.Element | null {
  const navigate = useNavigate();
  const { state, loading, dismiss, workspaceSlug } = useOnboarding();

  if (loading || state === null || state.dismissed_at !== null) return null;

  if (state.aha_reached_at !== null) {
    return (
      <AhaCard
        onDismiss={() => void dismiss()}
        onOpenInbox={() => navigate('/inbox')}
      />
    );
  }

  return (
    <ChecklistCard
      state={state}
      workspaceSlug={workspaceSlug}
      onDismiss={() => void dismiss()}
    />
  );
}
