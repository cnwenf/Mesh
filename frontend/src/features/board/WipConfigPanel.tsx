/**
 * WIP 限制配置面板(kanban.md §2.5 内嵌方案 board_settings.wip):
 * 每列 limit + enforcement(warn 软警告 / block 硬阻止);limit 清空 = 移除规则。
 * 写入经 PATCH /views/{id}/wip(§3.2);强制执行(move 命令拦截)属投影增量。
 */
import { useState } from 'react';
import { Button, Input, Select } from '../../design';
import { useT } from '../../i18n';
import type { BoardColumn, WipEnforcement } from './types';

interface WipConfigPanelProps {
  readonly columns: readonly BoardColumn[];
  readonly onSave: (groupKey: string, limit: number | null, enforcement: WipEnforcement) => Promise<void>;
}

export function WipConfigPanel(props: WipConfigPanelProps): React.JSX.Element {
  const { columns, onSave } = props;
  const t = useT();
  const [busyKey, setBusyKey] = useState<string | null>(null);

  return (
    <div className="mesh-wip-panel" data-testid="wip-config-panel">
      {columns.map((column) => {
        const label = column.key === '__dynamic__'
          ? t('board.wipDynamicColumn')
          : t(column.label);
        return (
          <WipRow
            key={column.key}
            groupKey={column.key}
            label={label}
            limit={column.wip?.limit ?? null}
            enforcement={column.wip?.enforcement ?? 'warn'}
            busy={busyKey === column.key}
            onSave={async (limit, enforcement) => {
              setBusyKey(column.key);
              try {
                await onSave(column.key, limit, enforcement);
              } finally {
                setBusyKey(null);
              }
            }}
          />
        );
      })}
    </div>
  );
}

interface WipRowProps {
  readonly groupKey: string;
  readonly label: string;
  readonly limit: number | null;
  readonly enforcement: WipEnforcement;
  readonly busy: boolean;
  readonly onSave: (limit: number | null, enforcement: WipEnforcement) => Promise<void>;
}

function WipRow(props: WipRowProps): React.JSX.Element {
  const { groupKey, label, limit, enforcement, busy, onSave } = props;
  const t = useT();
  const [limitText, setLimitText] = useState(limit === null ? '' : String(limit));
  const [enforcementDraft, setEnforcementDraft] = useState<WipEnforcement>(enforcement);

  const submit = async (): Promise<void> => {
    const parsed = limitText.trim() === '' ? null : Number.parseInt(limitText, 10);
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 1)) return;
    await onSave(parsed, enforcementDraft);
  };

  return (
    <div className="mesh-wip-panel__row" data-testid={`wip-row-${groupKey}`}>
      <span className="mesh-wip-panel__label">{label}</span>
      <Input
        label={t('board.wipLimitLabel')}
        type="number"
        min={1}
        value={limitText}
        onChange={(event) => setLimitText(event.target.value)}
        data-testid={`wip-limit-${groupKey}`}
      />
      <Select
        label={t('board.wipEnforcementLabel')}
        value={enforcementDraft}
        onChange={(event) => setEnforcementDraft(event.target.value as WipEnforcement)}
        data-testid={`wip-enforcement-${groupKey}`}
      >
        <option value="warn">{t('board.wipWarn')}</option>
        <option value="block">{t('board.wipBlock')}</option>
      </Select>
      <Button variant="secondary" disabled={busy} onClick={() => void submit()} data-testid={`wip-save-${groupKey}`}>
        {t('common.save')}
      </Button>
    </div>
  );
}
