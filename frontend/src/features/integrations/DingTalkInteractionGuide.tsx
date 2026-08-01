/** Human-facing DingTalk commands and approval-card lifecycle preview (integrations.md §4.3–§4.4). */
import { useState } from 'react';
import { Link } from 'react-router';
import { Button, Input, Select, StatusDot } from '../../design';
import { useT } from '../../i18n';
import type { DingTalkVerbosity } from './types';
import './integrations.css';

type CardPreviewState =
  | 'pending'
  | 'loading'
  | 'approved'
  | 'rejected'
  | 'duplicate'
  | 'expired'
  | 'forbidden'
  | 'failed';

const CARD_STATES: ReadonlyArray<CardPreviewState> = [
  'pending',
  'loading',
  'approved',
  'rejected',
  'duplicate',
  'expired',
  'forbidden',
  'failed',
];

// mesh-emoji-ok: 钉钉机器人对外停止反馈的原始文案，不作为 Mesh UI 图标使用
const DINGTALK_STOPPING_FEEDBACK = '⏳';
// mesh-emoji-ok: 钉钉机器人对外停止反馈的原始文案，不作为 Mesh UI 图标使用
const DINGTALK_STOPPED_FEEDBACK = '🛑';

export interface DingTalkInteractionGuideProps {
  readonly verbosity: DingTalkVerbosity;
  readonly ackTemplate: string;
}

export function DingTalkInteractionGuide(props: DingTalkInteractionGuideProps): React.JSX.Element {
  const { verbosity, ackTemplate } = props;
  const t = useT();
  const [command, setCommand] = useState('');
  const [cardState, setCardState] = useState<CardPreviewState>('pending');
  const terminal = !['pending', 'loading'].includes(cardState);
  const showFallback = cardState === 'expired' || cardState === 'failed';
  const cardTone =
    cardState === 'approved' || cardState === 'duplicate'
      ? 'success'
      : cardState === 'rejected' || cardState === 'failed'
        ? 'danger'
        : cardState === 'expired' || cardState === 'forbidden'
          ? 'warn'
          : 'info';

  return (
    <div className="mesh-integrations__interaction-guide">
      <section className="mesh-integrations__section" data-testid="dingtalk-command-help">
        <div className="mesh-integrations__header">
          <h3>{t('integrations.dingtalk.commands.title')}</h3>
          <span className="mesh-integrations__tag" data-testid="dingtalk-verbosity-preview">
            {t(`integrations.dingtalk.verbosity.${verbosity}`)}
          </span>
        </div>
        <dl className="mesh-integrations__kv">
          <dt>
            <code>/btw &lt;context&gt;</code>
          </dt>
          <dd>{t('integrations.dingtalk.commands.btwHelp')}</dd>
          <dt>
            <code>/stop [reason]</code>
          </dt>
          <dd>{t('integrations.dingtalk.commands.stopHelp')}</dd>
          <dt>
            <code>/help</code>
          </dt>
          <dd>{t('integrations.dingtalk.commands.helpHelp')}</dd>
        </dl>
        <div className="mesh-integrations__toolbar">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCommand('/btw ')}
            data-testid="dingtalk-command-btw"
          >
            /btw
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCommand('/stop ')}
            data-testid="dingtalk-command-stop"
          >
            /stop
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCommand('/help')}
            data-testid="dingtalk-command-help-button"
          >
            /help
          </Button>
        </div>
        <Input
          label={t('integrations.dingtalk.commands.inputLabel')}
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          hint={t('integrations.dingtalk.commands.inputHint')}
          data-testid="dingtalk-command-input"
        />
        <code className="mesh-integrations__command-preview" data-testid="dingtalk-command-preview">
          {command || t('integrations.dingtalk.commands.previewEmpty')}
        </code>

        <div className="mesh-integrations__preview-grid">
          <div className="mesh-integrations__preview-box" data-testid="dingtalk-ack-preview">
            <strong>{t('integrations.dingtalk.commands.ack')}</strong>
            <span>{ackTemplate || t('integrations.dingtalk.commands.ackDisabled')}</span>
          </div>
          <div className="mesh-integrations__preview-box" data-testid="dingtalk-position-preview">
            <strong>{t('integrations.dingtalk.commands.queue')}</strong>
            <span>{t('integrations.queue.position', { position: 2 })}</span>
          </div>
          <div className="mesh-integrations__preview-box" data-testid="dingtalk-stop-feedback">
            <strong>{t('integrations.dingtalk.commands.stopStages')}</strong>
            <span>
              {DINGTALK_STOPPING_FEEDBACK} {t('integrations.dingtalk.commands.stopping')}
            </span>
            <span>
              {DINGTALK_STOPPED_FEEDBACK} {t('integrations.dingtalk.commands.stopped')}
            </span>
          </div>
        </div>
      </section>

      <section className="mesh-integrations__section">
        <div className="mesh-integrations__header">
          <div>
            <h3>{t('integrations.dingtalk.card.previewTitle')}</h3>
            <p className="mesh-integrations__muted">
              {t('integrations.dingtalk.card.previewHint')}
            </p>
          </div>
          <Select
            label={t('integrations.dingtalk.card.stateLabel')}
            value={cardState}
            onChange={(event) => setCardState(event.target.value as CardPreviewState)}
            data-testid="dingtalk-card-state"
          >
            {CARD_STATES.map((state) => (
              <option key={state} value={state}>
                {t(`integrations.dingtalk.card.state.${state}`)}
              </option>
            ))}
          </Select>
        </div>
        <article
          className="mesh-integrations__approval-card"
          data-testid="dingtalk-notification-preview"
        >
          <div className="mesh-integrations__header">
            <strong>{t('integrations.dingtalk.notification.title')}</strong>
            <StatusDot
              tone={verbosity === 'progress' ? 'info' : 'neutral'}
              label={t(`integrations.dingtalk.verbosity.${verbosity}`)}
            />
          </div>
          <p data-testid="dingtalk-notification-body">
            {t(`integrations.dingtalk.notification.${verbosity}`)}
          </p>
          <p className="mesh-integrations__muted">
            {t('integrations.dingtalk.notification.sourceTruth')}
          </p>
        </article>
        <article className="mesh-integrations__approval-card" data-testid="dingtalk-card-preview">
          <div className="mesh-integrations__header">
            <strong>{t('integrations.dingtalk.card.title')}</strong>
            <StatusDot tone={cardTone} label={t(`integrations.dingtalk.card.state.${cardState}`)} />
          </div>

          {cardState !== 'forbidden' && (
            <dl className="mesh-integrations__kv">
              <dt>{t('integrations.dingtalk.card.action')}</dt>
              <dd>{t('integrations.dingtalk.card.sampleAction')}</dd>
              <dt>{t('integrations.dingtalk.card.permission')}</dt>
              <dd>{t('integrations.dingtalk.card.samplePermission')}</dd>
              <dt>{t('integrations.dingtalk.card.impact')}</dt>
              <dd>{t('integrations.dingtalk.card.sampleImpact')}</dd>
              <dt>{t('integrations.dingtalk.card.cost')}</dt>
              <dd>{t('integrations.dingtalk.card.sampleCost')}</dd>
              <dt>{t('integrations.dingtalk.card.resume')}</dt>
              <dd>{t('integrations.dingtalk.card.sampleResume')}</dd>
            </dl>
          )}

          <p className={`mesh-integrations__card-state mesh-integrations__card-state--${cardTone}`}>
            {t(`integrations.dingtalk.card.feedback.${cardState}`)}
          </p>

          <div className="mesh-integrations__toolbar">
            <Button
              variant="primary"
              size="sm"
              isLoading={cardState === 'loading'}
              disabled={cardState !== 'pending'}
              onClick={() => setCardState('loading')}
              data-testid="dingtalk-card-approve"
            >
              {t('integrations.dingtalk.card.approve')}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={cardState !== 'pending'}
              onClick={() => setCardState('loading')}
              data-testid="dingtalk-card-reject"
            >
              {t('integrations.dingtalk.card.reject')}
            </Button>
            {showFallback && (
              <Link to="/" data-testid="dingtalk-card-fallback">
                {t('integrations.dingtalk.card.backToMesh')}
              </Link>
            )}
          </div>
          {terminal && (
            <p className="mesh-integrations__muted">
              {t('integrations.dingtalk.card.terminalHint')}
            </p>
          )}
        </article>
      </section>
    </div>
  );
}
