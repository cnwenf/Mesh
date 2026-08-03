/**
 * 设备码授权确认页(auth.md §3.1.1 UX 增量,cli.md §3.2):CLI `mesh auth login`
 * 的浏览器批准侧。统一经 PublicFlowShell 公共流程外壳呈现(design-quality §4.4 /
 * §3.2 设备授权行:共用外壳、明确来源/权限/工作区/安全提示;过期·已处理·无工作区
 * 均有恢复动作)。标签页标题随语义变化(G19)。
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
import { Button, Input, PublicFlowShell, Select } from '../../design';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
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

  useDocumentTitle(t('title.device'));

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
    if (confirmation === null) return;
    // 单工作区自动绑定在点击时派生(不依赖 effect 时序——快速点击不得落空);
    // 多工作区必须已手选(无默认项)。
    const effectiveWorkspace =
      workspaceId !== ''
        ? workspaceId
        : confirmation.workspaces.length === 1
          ? confirmation.workspaces[0].id
          : '';
    if (effectiveWorkspace === '') return;
    // 批准绑定所录入的码(非预填值):防预填篡改导致批准他码。
    const code = codeInput.trim();
    try {
      const result = await approveDevice(client, code, effectiveWorkspace);
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

  // 未登录:品牌区不作链接(本态仅一个「去登录」链接,getByRole('link') 唯一)。
  if (token === null) {
    return (
      <PublicFlowShell
        brandLabel={t('brand.name')}
        skipLabel={t('a11y.skipLink')}
        title={t('device.title')}
      >
        <p className="mesh-public-flow__field-note">
          {t('device.loginRequired')}{' '}
          <Link to="/login?next=/device" className="mesh-public-flow__inline-link">
            {t('device.goLogin')}
          </Link>
        </p>
      </PublicFlowShell>
    );
  }

  const workspaces = confirmation?.workspaces ?? [];
  const noWorkspace = workspaces.length === 0;
  const mustChoose = workspaces.length > 1;
  const approveDisabled = noWorkspace || (mustChoose && workspaceId === '');

  return (
    <PublicFlowShell
      brandLabel={t('brand.name')}
      skipLabel={t('a11y.skipLink')}
      title={t('device.title')}
    >
      <form className="mesh-public-flow__form" onSubmit={onSubmitCode}>
        <Input
          id="device-code"
          label={t('device.codeLabel')}
          value={codeInput}
          size="lg"
          onChange={(event) => setCodeInput(event.target.value)}
          placeholder="WDJB-MJHT"
          autoComplete="off"
        />
        <Button type="submit" size="lg" disabled={phase === 'loading' || codeInput.trim() === ''}>
          {t('device.submitCode')}
        </Button>
      </form>

      {phase === 'not_found' ? (
        <div className="mesh-public-flow__alert" role="alert">
          <p className="mesh-public-flow__alert-message">{t('device.notFound')}</p>
        </div>
      ) : null}
      {phase === 'error' ? (
        <div className="mesh-public-flow__alert" role="alert">
          <p className="mesh-public-flow__alert-message">{errorMessage}</p>
        </div>
      ) : null}

      {phase === 'confirm' && confirmation !== null ? (
        <section className="mesh-public-flow__form" aria-label={t('device.confirmSection')}>
          <p className="mesh-public-flow__client">
            <strong>{confirmation.client_name}</strong> {t('device.requestsAccess')}
          </p>
          <p className="mesh-public-flow__notice" role="note">
            {t('device.securityNotice')}
          </p>
          <ul className="mesh-public-flow__scope-list" aria-label={t('device.scopeList')}>
            {confirmation.requested_scopes.map((entry) => (
              <li key={entry.scope}>
                <code>{entry.scope}</code> — {entry.description}
              </li>
            ))}
          </ul>

          {noWorkspace ? (
            <div className="mesh-public-flow__alert" role="alert">
              <p className="mesh-public-flow__alert-message">{t('device.noWorkspace')}</p>
            </div>
          ) : null}
          {workspaces.length === 1 ? (
            <p className="mesh-public-flow__field-note">
              {t('device.singleWorkspace')} <strong>{workspaces[0].name}</strong>
            </p>
          ) : null}
          {mustChoose ? (
            <Select
              id="device-workspace"
              label={t('device.chooseWorkspace')}
              value={workspaceId}
              onChange={(event) => setWorkspaceId(event.target.value)}
            >
              <option value="">{t('device.chooseWorkspacePlaceholder')}</option>
              {workspaces.map((ws) => (
                <option key={ws.id} value={ws.id}>
                  {ws.name} ({ws.my_role})
                </option>
              ))}
            </Select>
          ) : null}

          <div className="mesh-public-flow__actions">
            <Button onClick={() => void onApprove()} disabled={approveDisabled} autoFocus>
              {t('device.approve')}
            </Button>
            <Button onClick={() => void onDeny()} variant="secondary" type="button">
              {t('device.deny')}
            </Button>
          </div>
        </section>
      ) : null}

      {phase === 'done' ? (
        <p className="mesh-public-flow__result" role="status">
          {resultStatus === 'approved' ? t('device.resultApproved') : t('device.resultDenied')}
        </p>
      ) : null}
    </PublicFlowShell>
  );
}
