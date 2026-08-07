/**
 * 技能一绑多 agent 对话框(L247 批量操作,skills/bulk-bind):
 * 从活跃 agent 名册多选 → 逐项隔离绑定,部分成功以「成功 N,失败 M」汇总,
 * 失败项附前若干条 agent_id:code 便于定位(与 issues/bulk error marker 同约定)。
 */
import { useCallback, useMemo, useState } from 'react';
import { Button, Checkbox, Dialog, useToast } from '../../design';
import { useT } from '../../i18n';
import type { MeshApiClient } from '../../api';
import type { MemberSummary } from '../members/types';
import { bulkBindSkill } from './api';
import type { SkillInstallation } from './types';

interface BulkBindDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly installation: SkillInstallation;
  /** 可绑定的活跃 agent 名册。 */
  readonly agents: readonly MemberSummary[];
  readonly onDone: () => void;
}

/** 部分失败摘要(前 N 条 agent_id:code)。 */
const MAX_ERROR_MARKERS = 5;

export function BulkBindDialog(props: BulkBindDialogProps): React.JSX.Element {
  const { open, onClose, client, workspaceId, installation, agents, onDone } = props;
  const t = useT();
  const toast = useToast();
  const [checked, setChecked] = useState<ReadonlySet<string>>(() => new Set());
  const [isBusy, setIsBusy] = useState(false);

  const toggle = useCallback((agentId: string, value: boolean) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (value) next.add(agentId);
      else next.delete(agentId);
      return next;
    });
  }, []);

  const allChecked = agents.length > 0 && checked.size === agents.length;
  const someChecked = checked.size > 0 && !allChecked;

  const handleConfirm = useCallback(async () => {
    setIsBusy(true);
    try {
      const result = await bulkBindSkill(client, workspaceId, {
        skill_installation_id: installation.id,
        agent_ids: [...checked],
      });
      const summary = { succeeded: result.bound.length, failed: result.errors.length };
      const markers = result.errors
        .slice(0, MAX_ERROR_MARKERS)
        .map((entry) => `${entry.agent_id.slice(0, 8)}: ${entry.code}`)
        .join('; ');
      toast.addToast(`${t('skills.bulkBind.result', summary)}${markers ? ` — ${markers}` : ''}`, {
        tone: summary.failed > 0 ? 'warn' : 'success',
        closeLabel: t('common.close'),
      });
      setChecked(new Set());
      onDone();
      onClose();
    } catch {
      toast.addToast(t('skills.bulkBind.failed'), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setIsBusy(false);
    }
  }, [checked, client, installation.id, onClose, onDone, t, toast, workspaceId]);

  const rows = useMemo(
    () =>
      agents.map((agent) => (
        <Checkbox
          key={agent.id}
          label={agent.display_name}
          checked={checked.has(agent.id)}
          onChange={(event) => toggle(agent.id, event.target.checked)}
          data-testid={`bulk-bind-agent-${agent.id}`}
        />
      )),
    [agents, checked, toggle],
  );

  return (
    <Dialog open={open} onClose={onClose} title={t('skills.bulkBind.title')} closeLabel={t('common.close')}>
      <div className="mesh-skills__bulk-bind-body" data-testid="bulk-bind-body">
        <p className="mesh-text-body-sm">{t('skills.bulkBind.description')}</p>
        {agents.length === 0 ? (
          <p className="mesh-text-caption" data-testid="bulk-bind-empty">
            {t('skills.bulkBind.empty')}
          </p>
        ) : (
          <>
            <Checkbox
              label={t('skills.bulkBind.selectAll')}
              checked={allChecked}
              indeterminate={someChecked}
              onChange={() =>
                setChecked(allChecked ? new Set() : new Set(agents.map((agent) => agent.id)))
              }
              data-testid="bulk-bind-select-all"
            />
            {rows}
          </>
        )}
        <div className="mesh-issues__confirm-actions">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => void handleConfirm()}
            isLoading={isBusy}
            disabled={checked.size === 0}
            data-testid="bulk-bind-confirm"
          >
            {t('skills.bulkBind.submit', { count: checked.size })}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
