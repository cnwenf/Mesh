/** DingTalk-specific connection diagnostics and outbound smoke test (integrations.md §4.2). */
import { useCallback, useEffect, useState } from 'react';
import { useIntl } from 'react-intl';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import {
  Banner,
  Button,
  Dialog,
  ErrorState,
  Input,
  Select,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import {
  dingtalkCallbackUrl,
  getDingTalkStreamStatus,
  reconnectDingTalkStream,
  testDingTalkSend,
} from './api';
import { DINGTALK_STREAM_STATE_TONE, formatRelativeTime, toDingTalkStreamState } from './format';
import type {
  DingTalkReceiveMode,
  DingTalkStreamStatus,
  DingTalkTestSendResult,
  Integration,
} from './types';
import { DINGTALK_DEFAULT_ACK_TEMPLATE } from './types';
import './integrations.css';

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

function receiveMode(integration: Integration): DingTalkReceiveMode {
  return integration.config.receive_mode === 'http' ? 'http' : 'stream';
}

function unavailableStatus(error: MeshApiError): DingTalkStreamStatus | null {
  if (error.code !== 'stream_channel_unavailable') return null;
  return {
    state: toDingTalkStreamState(String(error.details?.state ?? 'down')),
    last_frame_at:
      typeof error.details?.last_frame_at === 'string' ? error.details.last_frame_at : null,
    last_attempt_at:
      typeof error.details?.last_attempt_at === 'string' ? error.details.last_attempt_at : null,
    backoff_seconds:
      typeof error.details?.backoff_seconds === 'number' ? error.details.backoff_seconds : null,
  };
}

export interface DingTalkOverviewPanelProps {
  readonly workspaceId: string;
  readonly integration: Integration;
  readonly isAdmin: boolean;
  readonly onEdit: () => void;
  readonly reloadKey?: number;
}

export function DingTalkOverviewPanel(props: DingTalkOverviewPanelProps): React.JSX.Element {
  const { workspaceId, integration, isAdmin, onEdit, reloadKey = 0 } = props;
  const t = useT();
  const intl = useIntl();
  const toast = useToast();
  const mode = receiveMode(integration);
  const [diagnosticKey, setDiagnosticKey] = useState(0);
  const [diagnosing, setDiagnosing] = useState(false);
  const [reconnectBusy, setReconnectBusy] = useState(false);
  const [streamStatus, setStreamStatus] = useState<DingTalkStreamStatus | null>(null);
  const [diagnosticErrorKey, setDiagnosticErrorKey] = useState<string | null>(null);
  const [testOpen, setTestOpen] = useState(false);
  const [conversationRef, setConversationRef] = useState('');
  const [conversationType, setConversationType] = useState<'group' | 'direct'>('group');
  const [userKey, setUserKey] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<DingTalkTestSendResult | null>(null);

  useEffect(() => {
    if (mode !== 'stream') {
      setStreamStatus(null);
      setDiagnosticErrorKey(null);
      return;
    }
    let cancelled = false;
    setDiagnosing(true);
    setDiagnosticErrorKey(null);
    void getDingTalkStreamStatus(newClient(), workspaceId, integration.id)
      .then((status) => {
        if (!cancelled) {
          setStreamStatus(status);
          setDiagnosticErrorKey(null);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof MeshApiError) {
          const status = unavailableStatus(error);
          if (status !== null) {
            setStreamStatus(status);
            setDiagnosticErrorKey(null);
            return;
          }
        }
        setStreamStatus(null);
        setDiagnosticErrorKey(
          error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown',
        );
      })
      .finally(() => {
        if (!cancelled) setDiagnosing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, workspaceId, integration.id, diagnosticKey, reloadKey]);

  const openTest = useCallback((): void => {
    setConversationRef('');
    setConversationType('group');
    setUserKey('');
    setTestResult(null);
    setTestOpen(true);
  }, []);

  const runTestSend = useCallback(async (): Promise<void> => {
    if (conversationRef.trim() === '') return;
    if (conversationType === 'direct' && userKey.trim() === '') return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testDingTalkSend(newClient(), workspaceId, integration.id, {
        conversation_ref: conversationRef.trim(),
        conversation_type: conversationType,
        ...(conversationType === 'direct' ? { user_key: userKey.trim() } : {}),
      });
      setTestResult(result);
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setTesting(false);
    }
  }, [conversationRef, conversationType, userKey, workspaceId, integration.id, toast, t]);

  const copyCallback = useCallback(async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(dingtalkCallbackUrl());
      toast.addToast(t('integrations.dingtalk.callbackCopied'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch {
      toast.addToast(t('error.unknown'), { tone: 'danger', closeLabel: t('common.close') });
    }
  }, [toast, t]);

  const requestReconnect = useCallback(async (): Promise<void> => {
    setReconnectBusy(true);
    try {
      await reconnectDingTalkStream(newClient(), workspaceId, integration.id);
      toast.addToast(t('integrations.dingtalk.reconnectRequested'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setDiagnosticKey((key) => key + 1);
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setReconnectBusy(false);
    }
  }, [workspaceId, integration.id, toast, t]);

  const lastFrameRelative =
    streamStatus?.last_frame_at === null || streamStatus?.last_frame_at === undefined
      ? null
      : formatRelativeTime(streamStatus.last_frame_at, Date.now(), intl.locale);
  const canSubmit =
    conversationRef.trim() !== '' && (conversationType === 'group' || userKey.trim() !== '');

  return (
    <section className="mesh-integrations__section" data-testid="dingtalk-connection-panel">
      <div className="mesh-integrations__header">
        <h3>{t('integrations.dingtalk.connectionTitle')}</h3>
        <div className="mesh-integrations__toolbar">
          {isAdmin && (
            <Button
              variant="secondary"
              size="sm"
              onClick={openTest}
              data-testid="dingtalk-test-send"
            >
              {t('integrations.dingtalk.testSend')}
            </Button>
          )}
          {mode === 'stream' && (
            <Button
              variant="secondary"
              size="sm"
              isLoading={diagnosing}
              onClick={() => setDiagnosticKey((key) => key + 1)}
              data-testid="dingtalk-diagnose"
            >
              {t('integrations.dingtalk.diagnose')}
            </Button>
          )}
        </div>
      </div>

      <dl className="mesh-integrations__kv">
        <dt>{t('integrations.dingtalk.receiveMode')}</dt>
        <dd data-testid="dingtalk-receive-mode">{t(`integrations.dingtalk.receive.${mode}`)}</dd>
        <dt>{t('integrations.dingtalk.verbosity')}</dt>
        <dd>
          {t(
            `integrations.dingtalk.verbosity.${String(integration.config.verbosity ?? 'final_only')}`,
          )}
        </dd>
        <dt>{t('integrations.dingtalk.ackTemplate')}</dt>
        <dd>{String(integration.config.ack_template ?? DINGTALK_DEFAULT_ACK_TEMPLATE)}</dd>
      </dl>

      {mode === 'stream' && streamStatus === null && diagnosing && (
        <Skeleton loadingLabel={t('integrations.dingtalk.diagnosing')} />
      )}
      {mode === 'stream' && streamStatus === null && diagnosticErrorKey !== null && (
        <div data-testid="dingtalk-diagnostic-error">
          <ErrorState
            title={t(diagnosticErrorKey)}
            retryLabel={t('common.retry')}
            onRetry={() => setDiagnosticKey((key) => key + 1)}
          />
        </div>
      )}
      {mode === 'stream' && streamStatus !== null && (
        <>
          <div className="mesh-integrations__toolbar">
            <span data-testid="dingtalk-stream-state">
              <StatusDot
                tone={DINGTALK_STREAM_STATE_TONE[streamStatus.state]}
                label={t(`integrations.dingtalk.stream.${streamStatus.state}`)}
              />
            </span>
            <span className="mesh-integrations__muted" data-testid="dingtalk-last-frame">
              {t('integrations.dingtalk.lastFrame')}:{' '}
              {streamStatus.last_frame_at ?? t('common.never')}
              {lastFrameRelative === null ? '' : ` (${lastFrameRelative})`}
            </span>
          </div>
          {(streamStatus.state === 'down' || streamStatus.state === 'reconnecting') && (
            <div data-testid="dingtalk-stream-alert">
              <Banner tone={streamStatus.state === 'down' ? 'danger' : 'warn'}>
                <span>
                  {t(
                    streamStatus.state === 'down'
                      ? 'integrations.dingtalk.streamDown'
                      : 'integrations.dingtalk.streamReconnecting',
                    { seconds: streamStatus.backoff_seconds ?? 0 },
                  )}
                </span>
                {isAdmin && (
                  <span className="mesh-integrations__toolbar">
                    <Button
                      variant="secondary"
                      size="sm"
                      isLoading={reconnectBusy}
                      onClick={() => void requestReconnect()}
                      data-testid="dingtalk-reconnect"
                    >
                      {t('integrations.dingtalk.reconnect')}
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={onEdit}
                      data-testid="dingtalk-edit-config"
                    >
                      {t('integrations.dingtalk.editConfig')}
                    </Button>
                  </span>
                )}
              </Banner>
            </div>
          )}
        </>
      )}

      {mode === 'http' && (
        <div className="mesh-integrations__field" data-testid="dingtalk-http-callback">
          <span>{t('integrations.dingtalk.callbackUrl')}</span>
          <code>{dingtalkCallbackUrl()}</code>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void copyCallback()}
            data-testid="dingtalk-copy-callback"
          >
            {t('common.copy')}
          </Button>
        </div>
      )}

      <Dialog
        open={testOpen}
        onClose={() => setTestOpen(false)}
        title={t('integrations.dingtalk.testSend')}
        closeLabel={t('common.close')}
      >
        <Select
          label={t('integrations.dingtalk.conversationType')}
          value={conversationType}
          onChange={(event) =>
            setConversationType(event.target.value === 'direct' ? 'direct' : 'group')
          }
          data-testid="dingtalk-test-conversation-type"
        >
          <option value="group">{t('integrations.dingtalk.conversation.group')}</option>
          <option value="direct">{t('integrations.dingtalk.conversation.direct')}</option>
        </Select>
        <Input
          label={t('integrations.dingtalk.conversationRef')}
          value={conversationRef}
          onChange={(event) => setConversationRef(event.target.value)}
          data-testid="dingtalk-test-conversation-ref"
        />
        {conversationType === 'direct' && (
          <Input
            label={t('integrations.dingtalk.userKey')}
            value={userKey}
            onChange={(event) => setUserKey(event.target.value)}
            data-testid="dingtalk-test-user-key"
          />
        )}
        {testResult !== null && (
          <Banner tone="success">
            <span data-testid="dingtalk-test-result">
              {t('integrations.dingtalk.testSent', { conversation: testResult.conversation_ref })}
            </span>
          </Banner>
        )}
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setTestOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            isLoading={testing}
            disabled={!canSubmit}
            onClick={() => void runTestSend()}
            data-testid="dingtalk-test-submit"
          >
            {t('integrations.dingtalk.send')}
          </Button>
        </div>
      </Dialog>
    </section>
  );
}
