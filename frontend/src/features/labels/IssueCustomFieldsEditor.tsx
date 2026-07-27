/**
 * issue 详情侧栏的自定义字段编辑面板(label-property.md §4.3)。
 * 按 def.type 渲染控件(text/textarea/url 输入、number、date/datetime、
 * 单选下拉、多选 chip、成员选择、布尔开关);每次变更单字段整体提交
 * (PUT,If-Match = issue.updated_at,§6.14);inactive 字段由后端隐藏。
 * issue.custom_field_changed 帧触发刷新,custom_field.* / option 帧同理。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { errorToI18nKey, MeshApiError } from '../../api';
import { Input, Select, useToast } from '../../design';
import { useT } from '../../i18n';
import type { RealtimeContextValue } from '../../shell/AppShell';
import type { RealtimeEventFrame } from '../../types/realtime';
import type { CustomFieldDef, CustomFieldOption } from './types';
import { workspaceCustomFieldsChannel } from './api';
import type { FieldValueInput, FieldValueListingEntry } from './associationTypes';
import { listIssueFieldValues, setIssueFieldValues } from './associationApi';
import './labels.css';

export interface FieldEditorMember {
  readonly id: string;
  readonly display_name: string;
  readonly member_type: 'human' | 'agent';
  readonly status: string;
}

export interface IssueCustomFieldsEditorProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly issueId: string;
  readonly issueUpdatedAt: string;
  readonly members: readonly FieldEditorMember[];
  readonly reloadKey: number;
  readonly realtime: RealtimeContextValue | null;
  /**
   * Called after a successful value commit: the write advances the issue's
   * updated_at/version (§5.4), so the page must refresh its issue to keep
   * the If-Match arbitration token fresh for subsequent edits.
   */
  readonly onIssueChanged?: () => void;
}

/** date input (YYYY-MM-DD) ↔ datetime-local input (YYYY-MM-DDTHH:mm) 转换。 */
function toDateInput(iso: string | null): string {
  // date 语义是日历日:后端以 UTC 午夜存储,取 UTC 日期部分即用户所填之日。
  return iso === null ? '' : iso.slice(0, 10);
}

const pad2 = (n: number): string => String(n).padStart(2, '0');

/** UTC ISO → datetime-local 的本地墙钟值(回显:UTC→local)。 */
function toDateTimeInput(iso: string | null): string {
  if (iso === null) return '';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '';
  return (
    `${parsed.getFullYear()}-${pad2(parsed.getMonth() + 1)}-${pad2(parsed.getDate())}` +
    `T${pad2(parsed.getHours())}:${pad2(parsed.getMinutes())}`
  );
}

/** datetime-local 本地墙钟值 → UTC ISO(提交:local→UTC,§2.8 存 UTC 时刻)。 */
function fromDateTimeInput(local: string): string | null {
  if (local === '') return null;
  const parsed = new Date(local); // 无时区后缀按本地时间解析
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString();
}

export function IssueCustomFieldsEditor(props: IssueCustomFieldsEditorProps): React.JSX.Element {
  const { client, workspaceId, issueId, issueUpdatedAt, members, realtime } = props;
  const t = useT();
  const toast = useToast();
  const [entries, setEntries] = useState<readonly FieldValueListingEntry[] | null>(null);

  // addToast 经 ref 持有:toast 上下文对象每次渲染换引用,直接进依赖会让
  // 加载 effect 在持续报错路径上无限重跑(BoardPage v0.11.6 同源修复)。
  const addToastRef = useRef(toast.addToast);
  addToastRef.current = toast.addToast;

  const load = useCallback(async () => {
    try {
      // 防御异常包络(非数组 data 视为空),避免渲染期崩溃。
      const entries = await listIssueFieldValues(client, issueId);
      setEntries(Array.isArray(entries) ? entries : []);
    } catch (error) {
      addToastRef.current(
        t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.network'),
        { tone: 'danger', closeLabel: t('common.close') },
      );
    }
  }, [client, issueId, t]);

  // On mount + whenever the issue's arbitration token (updated_at) changes,
  // i.e. AFTER the page's own reload has resolved. Not keyed on reloadKey:
  // child effects run before the parent's, so a reloadKey-driven refetch
  // would race the page's reload fetches (and the tests' positional stub).
  useEffect(() => {
    void load();
  }, [load, issueUpdatedAt]);

  // §3.5 增量:本 issue 的值变更 → 重拉(服务端为权威);定义/选项变更同理。
  useEffect(() => {
    if (realtime === null) return;
    realtime.client.subscribe(workspaceCustomFieldsChannel(workspaceId));
    const off = realtime.client.onFrame((frame: RealtimeEventFrame) => {
      if (frame.op !== 'event') return;
      const event = frame.event;
      if (
        event === 'issue.custom_field_changed' ||
        event.startsWith('custom_field')
      ) {
        void load();
      }
    });
    return () => {
      off();
      realtime.client.unsubscribe(workspaceCustomFieldsChannel(workspaceId));
    };
  }, [realtime, workspaceId, load]);

  const onIssueChanged = props.onIssueChanged;

  const commit = useCallback(
    async (input: FieldValueInput) => {
      try {
        const updated = await setIssueFieldValues(client, issueId, [input], issueUpdatedAt);
        setEntries(updated);
        onIssueChanged?.();
      } catch (error) {
        addToastRef.current(
          t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.network'),
          { tone: 'danger', closeLabel: t('common.close') },
        );
      }
    },
    [client, issueId, issueUpdatedAt, t, onIssueChanged],
  );

  if (entries === null) {
    return (
      <section className="mesh-issue-fields" aria-label={t('issueFields.sectionTitle')}>
        <h4 className="mesh-issues-detail__sidebar-heading">
          {t('issueFields.sectionTitle')}
        </h4>
        <p className="mesh-issue-fields__loading">{t('common.loading')}</p>
      </section>
    );
  }

  return (
    <section className="mesh-issue-fields" aria-label={t('issueFields.sectionTitle')}>
      <h4 className="mesh-issues-detail__sidebar-heading">{t('issueFields.sectionTitle')}</h4>
      {entries.length === 0 && (
        <p className="mesh-issue-fields__empty">{t('issueFields.empty')}</p>
      )}
      {entries.map((entry) => (
        <FieldControl
          // key 携带值身份(updated_at):服务端值变化(他端提交经实时重拉、
          // 或本端提交回读)时重挂载控件,defaultValue 随之刷新(受控等价)。
          key={entry.field_def.id + ':' + (entry.value?.updated_at ?? 'none')}
          def={entry.field_def}
          value={entry.value}
          members={members}
          onCommit={commit}
        />
      ))}
    </section>
  );
}

interface FieldControlProps {
  readonly def: CustomFieldDef;
  readonly value: FieldValueListingEntry['value'];
  readonly members: readonly FieldEditorMember[];
  readonly onCommit: (input: FieldValueInput) => Promise<void>;
}

function FieldControl(props: FieldControlProps): React.JSX.Element {
  const { def, value, members, onCommit } = props;
  const t = useT();
  const fieldDefId = def.id;
  const label = def.is_required ? `${def.name} *` : def.name;
  const testId = `issue-field-${def.field_key}`;

  switch (def.type) {
    case 'text':
    case 'url':
      return (
        <Input
          label={label}
          type={def.type === 'url' ? 'url' : 'text'}
          defaultValue={value?.value_text ?? ''}
          data-testid={testId}
          onBlur={(event) => {
            const next = event.target.value;
            if (next !== (value?.value_text ?? '')) {
              void onCommit({
                field_def_id: fieldDefId,
                value_text: next === '' ? null : next,
              });
            }
          }}
        />
      );
    case 'textarea':
      return (
        <label className="mesh-issue-fields__control">
          <span className="mesh-issue-fields__label">{label}</span>
          <textarea
            className="mesh-issue-fields__textarea"
            defaultValue={value?.value_text ?? ''}
            data-testid={testId}
            onBlur={(event) => {
              const next = event.target.value;
              if (next !== (value?.value_text ?? '')) {
                void onCommit({
                  field_def_id: fieldDefId,
                  value_text: next === '' ? null : next,
                });
              }
            }}
          />
        </label>
      );
    case 'number':
      return (
        <Input
          label={label}
          type="number"
          defaultValue={value?.value_number ?? ''}
          data-testid={testId}
          onBlur={(event) => {
            const raw = event.target.value;
            if (raw === '') {
              if (value?.value_number !== null && value !== null) {
                void onCommit({ field_def_id: fieldDefId, value_number: null });
              }
              return;
            }
            const next = Number(raw);
            if (Number.isFinite(next) && next !== value?.value_number) {
              void onCommit({ field_def_id: fieldDefId, value_number: next });
            }
          }}
        />
      );
    case 'date':
      return (
        <Input
          label={label}
          type="date"
          defaultValue={toDateInput(value?.value_date ?? null)}
          data-testid={testId}
          onChange={(event) => {
            const next = event.target.value;
            void onCommit({
              field_def_id: fieldDefId,
              value_date: next === '' ? null : next,
            });
          }}
        />
      );
    case 'datetime':
      return (
        <Input
          label={label}
          type="datetime-local"
          defaultValue={toDateTimeInput(value?.value_date ?? null)}
          data-testid={testId}
          onChange={(event) => {
            const next = event.target.value;
            void onCommit({
              field_def_id: fieldDefId,
              value_date: fromDateTimeInput(next),
            });
          }}
        />
      );
    case 'boolean':
      return (
        <label className="mesh-issue-fields__checkbox-row">
          <input
            type="checkbox"
            checked={value?.value_boolean ?? false}
            data-testid={testId}
            onChange={(event) =>
              void onCommit({
                field_def_id: fieldDefId,
                value_boolean: event.target.checked,
              })
            }
          />
          <span>{label}</span>
        </label>
      );
    case 'member':
      return (
        <Select
          label={label}
          value={value?.value_member_id ?? ''}
          data-testid={testId}
          onChange={(event) =>
            void onCommit({
              field_def_id: fieldDefId,
              value_member_id: event.target.value === '' ? null : event.target.value,
            })
          }
        >
          <option value="">{t('issueFields.memberNone')}</option>
          {members
            .filter((m) => m.status === 'active')
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
                {m.member_type === 'agent' ? ` (${t('issues.agentBadge')})` : ''}
              </option>
            ))}
        </Select>
      );
    case 'single_select': {
      const selectedOption = def.options.find((o) => o.id === value?.value_json);
      return (
        <div className="mesh-issue-fields__select-wrap">
          {selectedOption?.color != null && (
            <span
              className="mesh-labels__dot mesh-issue-fields__select-dot"
              style={{ backgroundColor: selectedOption.color }}
              aria-hidden="true"
            />
          )}
          <Select
            label={label}
            value={typeof value?.value_json === 'string' ? value.value_json : ''}
          data-testid={testId}
          onChange={(event) =>
            void onCommit({
              field_def_id: fieldDefId,
              value_json: event.target.value === '' ? null : event.target.value,
            })
          }
        >
            <option value="">{t('issueFields.selectNone')}</option>
            {def.options
              .filter((o: CustomFieldOption) => o.is_active)
              .map((o: CustomFieldOption) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
          </Select>
        </div>
      );
    }
    case 'multi_select': {
      const selected: string[] = Array.isArray(value?.value_json)
        ? (value?.value_json as string[])
        : [];
      const toggle = (optionId: string) => {
        const next = selected.includes(optionId)
          ? selected.filter((id) => id !== optionId)
          : [...selected, optionId];
        void onCommit({ field_def_id: fieldDefId, value_json: next });
      };
      return (
        <fieldset className="mesh-issue-fields__multi">
          <legend>{label}</legend>
          {def.options
            .filter((o: CustomFieldOption) => o.is_active)
            .map((o: CustomFieldOption) => (
              <label key={o.id} className="mesh-issue-fields__multi-option">
                <input
                  type="checkbox"
                  checked={selected.includes(o.id)}
                  data-testid={`${testId}-${o.name}`}
                  onChange={() => toggle(o.id)}
                />
                {o.color !== null && (
                  <span
                    className="mesh-labels__dot"
                    style={{ backgroundColor: o.color }}
                    aria-hidden="true"
                  />
                )}
                {o.name}
              </label>
            ))}
        </fieldset>
      );
    }
    default:
      return <span />;
  }
}
