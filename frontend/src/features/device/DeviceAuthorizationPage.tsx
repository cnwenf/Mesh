/**
 * 设备码授权确认页(auth.md §3.1.1 UX 增量,cli.md §3.2):CLI `mesh auth login`
 * 的浏览器批准侧。
 *
 * 契约要点(写死):
 * - user_code **手工录入**——`?user_code=` 预填仅便利,输入控件始终可见且提交时
 *   校验录入值(防无意识一键批准,RFC 8628 §5.5 钓鱼防护);
 * - 批准**仅绑定所录入的码**;工作区**显式选定**(0 个 → 禁用批准并提示;1 个 →
 *   自动绑定并明示;多个 → 必选,无默认项);
 * - scope 人类可读全量枚举(服务端取交后) + 醒目安全提示;
 * - 批准为默认焦点但**非默认确认**(防回车误批)。
 */
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { MeshApiClient } from '../../api/client';
import { approveDevice, denyDevice, fetchDeviceConfirmation } from '../../api/auth';
import type { DeviceConfirmation } from '../../api/auth';
import { getApiClient } from '../../api/instance';
import { Button, Input } from '../../design';
import { useT } from '../../i18n';
import { useAuthStore } from '../../state/authStore';

type Phase = 'input' | 'loading' | 'confirm' | 'not_found' | 'done' | 'error';

export function DeviceAuthorizationPage(): React.JSX.Element {
  const t = useT();
  const token = useAuthStore((state) => state.token);
  const [searchParams] = useSearchParams();
  const prefilled = searchParams.get('user_code') ?? '';

  const [codeInput, setCodeInput] = useState(prefilled);
  const [phase, setPhase] = useState<Phase>('input');
  const [confirmation, setConfirmation] = useState<DeviceConfirmation | null>(null);
  const [workspaceId, setWorkspaceId] = useState('');
  const [resultStatus, setResultStatus] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  const client: MeshApiClient = getApiClient();

  // 预填仅便利:有预填值时自动拉取确认页数据,输入控件仍可见可改。
  useEffect(() => {
    if (prefilled === '' || token === null) return;
    void loadConfirmation(prefilled);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefilled, token]);

  // 单工作区自动绑定(并明示);多工作区强制手选(无默认项)。
  useEffect(() => {
    if (confirmation === null) return;
    if (confirmation.workspaces.length === 1) {
      setWorkspaceId(confirmation.workspaces[0].id);
    } else {
      setWorkspaceId('');
    }
  }, [confirmation]);

  async function loadConfirmation(code: string): Promise<void> {
    setPhase('loading');
    setErrorMessage('');
    try {
      const data = await fetchDeviceConfirmation(client, code);
      setConfirmation(data);
      setPhase('confirm');
    } catch {
      setPhase('not_found');
    }
  }

  function onSubmitCode(event: FormEvent): void {
    event.preventDefault();
    const code = codeInput.trim();
    if (code === '') return;
    void loadConfirmation(code);
  }

  async function onApprove(): Promise<void> {
    if (confirmation === null || workspaceId === '') return;
    // 批准绑定所录入的码(非预填值):防预填篡改导致批准他码。
    const code = codeInput.trim();
    try {
      const result = await approveDevice(client, code, workspaceId);
      setResultStatus(result.status);
      setPhase('done');
    } catch {
      setErrorMessage(t('device.errorApprove'));
      setPhase('error');
    }
  }

  async function onDeny(): Promise<void> {
    const code = codeInput.trim();
    if (code === '') return;
    try {
      const result = await denyDevice(client, code);
      setResultStatus(result.status);
      setPhase('done');
    } catch {
      setErrorMessage(t('device.errorDeny'));
      setPhase('error');
    }
  }

  if (token === null) {
    return (
      <main style={{ maxWidth: 420, margin: '80px auto', padding: '0 16px' }}>
        <h1>{t('device.title')}</h1>
        <p>
          {t('device.loginRequired')}{' '}
          <Link to="/login?next=/device">{t('device.goLogin')}</Link>
        </p>
      </main>
    );
  }

  const workspaces = confirmation?.workspaces ?? [];
  const noWorkspace = workspaces.length === 0;
  const mustChoose = workspaces.length > 1;
  const approveDisabled = noWorkspace || (mustChoose && workspaceId === '');

  return (
    <main style={{ maxWidth: 480, margin: '80px auto', padding: '0 16px' }}>
      <h1>{t('device.title')}</h1>

      <form onSubmit={onSubmitCode}>
        <Input
          id="device-code"
          label={t('device.codeLabel')}
          value={codeInput}
          onChange={(event) => setCodeInput(event.target.value)}
          placeholder="WDJB-MJHT"
          autoComplete="off"
        />
        <Button type="submit" disabled={phase === 'loading' || codeInput.trim() === ''}>
          {t('device.submitCode')}
        </Button>
      </form>

      {phase === 'not_found' && <p role="alert">{t('device.notFound')}</p>}
      {phase === 'error' && <p role="alert">{errorMessage}</p>}

      {phase === 'confirm' && confirmation !== null && (
        <section aria-label={t('device.confirmSection')}>
          <p>
            <strong>{confirmation.client_name}</strong> {t('device.requestsAccess')}
          </p>
          <p role="note">{t('device.securityNotice')}</p>
          <ul aria-label={t('device.scopeList')}>
            {confirmation.requested_scopes.map((entry) => (
              <li key={entry.scope}>
                <code>{entry.scope}</code> — {entry.description}
              </li>
            ))}
          </ul>

          {noWorkspace && <p role="alert">{t('device.noWorkspace')}</p>}
          {workspaces.length === 1 && (
            <p>
              {t('device.singleWorkspace')} <strong>{workspaces[0].name}</strong>
            </p>
          )}
          {mustChoose && (
            <>
              <label htmlFor="device-workspace">{t('device.chooseWorkspace')}</label>
              <select
                id="device-workspace"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
              >
                <option value="">{t('device.chooseWorkspacePlaceholder')}</option>
                {workspaces.map((ws) => (
                  <option key={ws.id} value={ws.id}>
                    {ws.name} ({ws.my_role})
                  </option>
                ))}
              </select>
            </>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <Button onClick={() => void onApprove()} disabled={approveDisabled} autoFocus>
              {t('device.approve')}
            </Button>
            <Button onClick={() => void onDeny()} type="button">
              {t('device.deny')}
            </Button>
          </div>
        </section>
      )}

      {phase === 'done' && (
        <p role="status">
          {resultStatus === 'approved' ? t('device.resultApproved') : t('device.resultDenied')}
        </p>
      )}
    </main>
  );
}
