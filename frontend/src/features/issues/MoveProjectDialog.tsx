/**
 * 跨项目迁移预览确认对话框(issue.md §4.3 / §3.8 两步式契约第二步)。
 * 自 IssueDetailPage 抽出以维持单文件聚焦(<800 行);行为 / testid 不变。
 */
import { useCallback, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { moveIssue } from './api';
import type { MovePreview, MovePreviewField } from './types';
import { isMovePreview } from './types';
import './issues.css';

/**
 * 迁移预览字段技术键 → 可读文案映射键(LOW-2):后端 `field` 为 snake_case 技术键
 * (issue.md §3.8),UI 以本地化字段名呈现;未知键回退原始值(不中断渲染)。
 */
const MOVE_FIELD_LABEL_KEYS: Readonly<Record<string, string>> = {
  status: 'issues.move.field.status',
  milestone_id: 'issues.move.field.milestone_id',
  cycle_id: 'issues.move.field.cycle_id',
  labels: 'issues.move.field.labels',
  custom_field_values: 'issues.move.field.custom_field_values',
};

/**
 * 迁移预览 reason → 可读文案映射键(LOW-2):reason 取自 issue.md §3.8 契约词汇
 * (含后端模块未就绪占位码);未知 reason 回退原始值(后端新增词汇前不中断渲染)。
 */
const MOVE_REASON_LABEL_KEYS: Readonly<Record<string, string>> = {
  '项目私有 status → 目标项目同 category 默认 status': 'issues.move.reason.statusMapped',
  项目私有里程碑: 'issues.move.reason.projectMilestone',
  项目绑定的周期: 'issues.move.reason.projectCycle',
  项目级标签: 'issues.move.reason.projectLabels',
  项目级自定义字段值: 'issues.move.reason.projectCustomFields',
  label_module_pending: 'issues.move.reason.labelModulePending',
  custom_field_module_pending: 'issues.move.reason.customFieldModulePending',
};

function moveFieldLabel(t: (key: string) => string, field: string): string {
  const key = MOVE_FIELD_LABEL_KEYS[field];
  return key !== undefined ? t(key) : field;
}

function moveReasonLabel(t: (key: string) => string, reason: string): string {
  const key = MOVE_REASON_LABEL_KEYS[reason];
  return key !== undefined ? t(key) : reason;
}

export interface MoveDialogProps {
  readonly preview: MovePreview;
  /** 解析后的目标项目显示名(null 目标 = 工作区收件箱);对话框须标明迁移去向(§4.3/§3.8)。 */
  readonly targetProjectName: string;
  readonly version: number;
  readonly onCancel: () => void;
  readonly onDone: () => void;
  /** LOW-3:422 move_confirmation_required 携最新预览时回写父级(保持对话框,重渲染预览)。 */
  readonly onPreviewRefresh: (preview: MovePreview) => void;
}

export function MoveProjectDialog(props: MoveDialogProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [isBusy, setIsBusy] = useState(false);
  const { preview } = props;

  const confirm = useCallback(async () => {
    setIsBusy(true);
    try {
      await moveIssue(client, preview.issue_id, {
        target_project_id: preview.target_project_id,
        confirm: true,
        version: props.version,
      });
      toast.addToast(t('issues.move.success'), { tone: 'success', closeLabel: t('common.close') });
      props.onDone();
    } catch (err: unknown) {
      // LOW-3:预览过期 → 422 move_confirmation_required(契约:details.preview 携最新预览)。
      // 以最新预览重渲染并保持对话框,不降级为通用 toast + 关闭(issue.md §3.8/README §6.14)。
      if (err instanceof MeshApiError && err.code === 'move_confirmation_required') {
        const freshPreview = err.details?.preview;
        if (isMovePreview(freshPreview)) {
          props.onPreviewRefresh(freshPreview);
          return;
        }
      }
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      props.onCancel();
    } finally {
      setIsBusy(false);
    }
  }, [client, preview, props, toast, t]);

  return (
    <div className="mesh-issues__move-overlay" data-testid="move-dialog">
      <div className="mesh-issues__move-dialog" role="dialog" aria-label={t('issues.move.title')}>
        <h3>{t('issues.move.title')}</h3>
        <p className="mesh-issues__move-identifier">{preview.identifier}</p>
        <p className="mesh-issues__move-target" data-testid="move-target">
          {t('issues.move.targetProject', { name: props.targetProjectName })}
        </p>
        {preview.mapped_fields.length > 0 ? (
          <section data-testid="move-mapped">
            <h4>{t('issues.move.mapped')}</h4>
            <ul>
              {preview.mapped_fields.map((field: MovePreviewField) => {
                const from = field.from as { name?: string } | undefined;
                const to = field.to as { name?: string } | undefined;
                return (
                  <li key={field.field}>
                    {moveFieldLabel(t, field.field)}: {from?.name ?? '?'} → {to?.name ?? '?'}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
        {preview.cleared_fields.length > 0 ? (
          <section data-testid="move-cleared">
            <h4>{t('issues.move.cleared')}</h4>
            <ul>
              {preview.cleared_fields.map((field: MovePreviewField) => (
                <li key={field.field}>
                  {moveFieldLabel(t, field.field)}({moveReasonLabel(t, field.reason)})
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <p className="mesh-issues__move-kept">{t('issues.move.keptNote')}</p>
        <div className="mesh-issues__move-actions">
          <Button variant="ghost" onClick={props.onCancel} data-testid="move-cancel">
            {t('issues.move.cancel')}
          </Button>
          <Button onClick={() => void confirm()} disabled={isBusy} data-testid="move-confirm">
            {t('issues.move.confirm')}
          </Button>
        </div>
      </div>
    </div>
  );
}
