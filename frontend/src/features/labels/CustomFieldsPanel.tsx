/**
 * 自定义字段管理面板(label-property.md §4.1/§4.3/§4.5 定义层):
 * 字段列表(名称 | 类型 | 作用域 | 必填 | 状态 | 操作)、新建/编辑字段对话框
 * (类型选择 → 必填开关/展示排序;枚举型附选项增删改与配色)、停用/启用与删除。
 * 实时 custom_field.* / custom_field_option.* 帧触发列表刷新(§3.5)。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { MeshApiError, errorToI18nKey } from '../../api';
import { Button, Dialog, EmptyState, ErrorState, IconButton, Input, Select, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import {
  createCustomField,
  createOption,
  deleteCustomField,
  deleteOption,
  listCustomFields,
  projectChannel,
  updateCustomField,
  updateOption,
  workspaceCustomFieldsChannel,
} from './api';
import { ColorPicker, isValidHexColor } from './ColorPicker';
import { CUSTOM_FIELD_TYPES, SELECT_FIELD_TYPES } from './types';
import type { CustomFieldDef, CustomFieldType, OptionInput } from './types';

interface CustomFieldsPanelProps {
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  /** 传入即项目设置上下文:新建为项目级字段,并订阅 project:{id} 频道。 */
  readonly projectId?: string;
}

interface FieldFormState {
  name: string;
  fieldKey: string;
  type: CustomFieldType;
  isRequired: boolean;
  position: string;
  options: OptionDraft[];
}

interface OptionDraft {
  readonly key: string;
  name: string;
  color: string;
}

const EMPTY_FORM: FieldFormState = {
  name: '',
  fieldKey: '',
  type: 'text',
  isRequired: false,
  position: '0',
  options: [],
};

const FIELD_KEY_PATTERN = /^[a-z][a-z0-9_]{0,49}$/;

let optionDraftSeq = 0;
function newOptionDraft(): OptionDraft {
  optionDraftSeq += 1;
  return {
    key: 'draft-' + String(optionDraftSeq),
    name: '',
    // mesh-data-color: 选项数据色板默认值(数据色非主题取色,theme.md §2.5 合法例外)
    color: '#3e63dd',
  };
}

async function fetchAllFields(
  client: MeshApiClient,
  workspaceId: string,
  projectId?: string,
): Promise<readonly CustomFieldDef[]> {
  const collected: CustomFieldDef[] = [];
  let cursor: string | null = null;
  do {
    const page = await listCustomFields(client, workspaceId, {
      project_id: projectId,
      limit: 200,
      cursor: cursor ?? undefined,
    });
    collected.push(...page.data);
    cursor = page.nextCursor;
  } while (cursor !== null);
  return collected;
}

export function CustomFieldsPanel(props: CustomFieldsPanelProps): React.JSX.Element {
  const { client, workspaceId, projectId } = props;
  const t = useT();
  const { addToast } = useToast();
  const realtime = useRealtimeContext();

  const [fields, setFields] = useState<readonly CustomFieldDef[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  const [dialogMode, setDialogMode] = useState<'closed' | 'create' | 'edit'>('closed');
  const [editing, setEditing] = useState<CustomFieldDef | null>(null);
  const [form, setForm] = useState<FieldFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [deleting, setDeleting] = useState<CustomFieldDef | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [optionsFor, setOptionsFor] = useState<CustomFieldDef | null>(null);

  const refresh = useCallback(() => setRefreshTick((tick) => tick + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoadError(false);
    fetchAllFields(client, workspaceId, projectId)
      .then((items) => {
        if (!cancelled) setFields(items);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, projectId, refreshTick]);

  useEffect(() => {
    if (realtime === null) return;
    const channels = [workspaceCustomFieldsChannel(workspaceId)];
    if (projectId !== undefined) channels.push(projectChannel(projectId));
    for (const channel of channels) realtime.client.subscribe(channel);
    const offFrame = realtime.client.onFrame((frame) => {
      if (!channels.includes(frame.channel)) return;
      if (frame.event.startsWith('custom_field')) refresh();
    });
    return () => {
      offFrame();
      for (const channel of channels) realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId, projectId, refresh]);

  const openCreate = (): void => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setDialogMode('create');
  };

  const openEdit = (field: CustomFieldDef): void => {
    setEditing(field);
    setForm({
      name: field.name,
      fieldKey: field.field_key,
      type: field.type,
      isRequired: field.is_required,
      position: String(field.position),
      options: [],
    });
    setFormError(null);
    setDialogMode('edit');
  };

  const closeDialog = (): void => setDialogMode('closed');

  const isSelectType = SELECT_FIELD_TYPES.includes(form.type);

  const handleSave = async (): Promise<void> => {
    const name = form.name.trim();
    if (name.length === 0 || name.length > 100) {
      setFormError(t('fields.errors.nameLength'));
      return;
    }
    const position = Number(form.position);
    if (!Number.isFinite(position)) {
      setFormError(t('fields.errors.positionInvalid'));
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      if (dialogMode === 'create') {
        if (!FIELD_KEY_PATTERN.test(form.fieldKey)) {
          setFormError(t('fields.errors.fieldKeyFormat'));
          setIsSaving(false);
          return;
        }
        if (isSelectType) {
          const optionNames = form.options.map((option) => option.name.trim());
          if (optionNames.some((optionName) => optionName.length === 0)) {
            setFormError(t('fields.errors.optionNameEmpty'));
            setIsSaving(false);
            return;
          }
          if (new Set(optionNames).size !== optionNames.length) {
            setFormError(t('fields.errors.optionNameDup'));
            setIsSaving(false);
            return;
          }
        }
        const initialOptions: readonly OptionInput[] = isSelectType
          ? form.options.map((option, index) => ({
              name: option.name.trim(),
              color: isValidHexColor(option.color) ? option.color : null,
              position: index,
            }))
          : [];
        await createCustomField(client, workspaceId, {
          name,
          field_key: form.fieldKey,
          type: form.type,
          project_id: projectId ?? null,
          is_required: form.isRequired,
          position,
          options: initialOptions,
        });
        addToast(t('fields.createdToast'), { tone: 'success', closeLabel: t('common.close') });
      } else if (editing !== null) {
        await updateCustomField(
          client,
          editing.id,
          { name, is_required: form.isRequired, position },
          editing.updated_at,
        );
        addToast(t('fields.updatedToast'), { tone: 'success', closeLabel: t('common.close') });
      }
      closeDialog();
      refresh();
    } catch (err) {
      setFormError(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleToggleActive = async (field: CustomFieldDef): Promise<void> => {
    try {
      await updateCustomField(client, field.id, { is_active: !field.is_active }, field.updated_at);
      addToast(
        field.is_active ? t('fields.deactivatedToast') : t('fields.activatedToast'),
        { tone: 'info', closeLabel: t('common.close') },
      );
      refresh();
    } catch (err) {
      addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (deleting === null) return;
    setIsDeleting(true);
    try {
      await deleteCustomField(client, deleting.id);
      addToast(t('fields.deletedToast'), { tone: 'info', closeLabel: t('common.close') });
      setDeleting(null);
      refresh();
    } catch (err) {
      addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <section aria-label={t('fields.sectionTitle')} data-testid="custom-fields-panel">
      <div className="mesh-labels__header">
        <h3>{t('fields.sectionTitle')}</h3>
        <Button size="sm" onClick={openCreate} data-testid="fields-create">
          {t('fields.createButton')}
        </Button>
      </div>

      {fields === null ? (
        loadError ? (
          <ErrorState
            title={t('state.errorTitle')}
            description={t('state.errorDescription')}
            onRetry={refresh}
            retryLabel={t('common.retry')}
          />
        ) : (
          <Skeleton loadingLabel={t('state.loading')} />
        )
      ) : fields.length === 0 ? (
        <EmptyState title={t('fields.emptyTitle')} description={t('fields.emptyDescription')} />
      ) : (
        <ul className="mesh-labels__list" data-testid="fields-list">
          {fields.map((field) => (
            <li key={field.id} className="mesh-labels__row" data-testid={'field-row-' + field.field_key}>
              <span className="mesh-labels__name">{field.name}</span>
              <span className="mesh-labels__hex">{t('fields.type.' + field.type)}</span>
              <span className="mesh-labels__hex">{field.field_key}</span>
              {field.is_required ? <span className="mesh-labels__scope">{t('fields.requiredBadge')}</span> : null}
              <span className="mesh-labels__scope">
                {field.project_id === null ? t('labels.scopeWorkspace') : t('labels.scopeProject')}
              </span>
              <span className={'mesh-labels__scope' + (field.is_active ? '' : ' mesh-labels__scope--inactive')}>
                {field.is_active ? t('fields.statusActive') : t('fields.statusInactive')}
              </span>
              <span className="mesh-labels__actions">
                {SELECT_FIELD_TYPES.includes(field.type) ? (
                  <IconButton
                    label={t('fields.optionsButton', { name: field.name })}
                    size="sm"
                    variant="ghost"
                    data-testid={'field-options-' + field.field_key}
                    onClick={() => setOptionsFor(field)}
                  >
                    {t('fields.optionsGlyph')}
                  </IconButton>
                ) : null}
                <IconButton
                  label={t('labels.editButton', { name: field.name })}
                  size="sm"
                  variant="ghost"
                  data-testid={'field-edit-' + field.field_key}
                  onClick={() => openEdit(field)}
                >
                  {t('labels.editGlyph')}
                </IconButton>
                <IconButton
                  label={
                    field.is_active
                      ? t('fields.deactivateButton', { name: field.name })
                      : t('fields.activateButton', { name: field.name })
                  }
                  size="sm"
                  variant="ghost"
                  data-testid={'field-toggle-' + field.field_key}
                  onClick={() => void handleToggleActive(field)}
                >
                  {field.is_active ? t('fields.deactivateGlyph') : t('fields.activateGlyph')}
                </IconButton>
                <IconButton
                  label={t('labels.deleteButton', { name: field.name })}
                  size="sm"
                  variant="danger"
                  data-testid={'field-delete-' + field.field_key}
                  onClick={() => setDeleting(field)}
                >
                  {t('labels.deleteGlyph')}
                </IconButton>
              </span>
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={dialogMode !== 'closed'}
        onClose={closeDialog}
        title={dialogMode === 'create' ? t('fields.dialog.createTitle') : t('fields.dialog.editTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-labels__dialog-body">
          <Input
            label={t('fields.dialog.nameLabel')}
            value={form.name}
            maxLength={100}
            data-testid="field-name-input"
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
          {dialogMode === 'create' ? (
            <>
              <Input
                label={t('fields.dialog.keyLabel')}
                hint={t('fields.dialog.keyHint')}
                value={form.fieldKey}
                data-testid="field-key-input"
                onChange={(event) => setForm({ ...form, fieldKey: event.target.value })}
              />
              <Select
                label={t('fields.dialog.typeLabel')}
                value={form.type}
                data-testid="field-type-select"
                onChange={(event) =>
                  setForm({
                    ...form,
                    type: event.target.value as CustomFieldType,
                    options:
                      SELECT_FIELD_TYPES.includes(event.target.value as CustomFieldType) &&
                      form.options.length === 0
                        ? [newOptionDraft()]
                        : form.options,
                  })
                }
              >
                {CUSTOM_FIELD_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {t('fields.type.' + type)}
                  </option>
                ))}
              </Select>
            </>
          ) : null}
          <label className="mesh-labels__checkbox">
            <input
              type="checkbox"
              checked={form.isRequired}
              data-testid="field-required-checkbox"
              onChange={(event) => setForm({ ...form, isRequired: event.target.checked })}
            />
            {t('fields.dialog.requiredLabel')}
          </label>
          <Input
            label={t('fields.dialog.positionLabel')}
            value={form.position}
            inputMode="decimal"
            data-testid="field-position-input"
            onChange={(event) => setForm({ ...form, position: event.target.value })}
          />
          {dialogMode === 'create' && isSelectType ? (
            <fieldset className="mesh-labels__options-editor" data-testid="field-options-editor">
              <legend>{t('fields.dialog.optionsLegend')}</legend>
              {form.options.map((option, index) => (
                <div key={option.key} className="mesh-labels__option-draft">
                  <Input
                    label={t('fields.dialog.optionNameLabel', { index: index + 1 })}
                    value={option.name}
                    data-testid={'field-option-name-' + index}
                    onChange={(event) => {
                      const options = form.options.map((item, itemIndex) =>
                        itemIndex === index ? { ...item, name: event.target.value } : item,
                      );
                      setForm({ ...form, options });
                    }}
                  />
                  <Input
                    label={t('fields.dialog.optionColorLabel')}
                    value={option.color}
                    data-testid={'field-option-color-' + index}
                    onChange={(event) => {
                      const options = form.options.map((item, itemIndex) =>
                        itemIndex === index ? { ...item, color: event.target.value } : item,
                      );
                      setForm({ ...form, options });
                    }}
                  />
                  <IconButton
                    label={t('fields.dialog.removeOptionLabel')}
                    size="sm"
                    variant="danger"
                    data-testid={'field-option-remove-' + index}
                    onClick={() =>
                      setForm({ ...form, options: form.options.filter((_, i) => i !== index) })
                    }
                  >
                    {t('labels.deleteGlyph')}
                  </IconButton>
                </div>
              ))}
              <Button
                size="sm"
                variant="secondary"
                data-testid="field-option-add"
                onClick={() => setForm({ ...form, options: [...form.options, newOptionDraft()] })}
              >
                {t('fields.dialog.addOption')}
              </Button>
            </fieldset>
          ) : null}
          {formError !== null ? (
            <p role="alert" data-testid="field-form-error">
              {formError}
            </p>
          ) : null}
          <div className="mesh-labels__dialog-footer">
            <Button variant="secondary" onClick={closeDialog}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void handleSave()} isLoading={isSaving} data-testid="field-save">
              {t('common.save')}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={t('fields.deleteTitle')}
        closeLabel={t('common.close')}
      >
        <div className="mesh-labels__dialog-body">
          <p data-testid="field-delete-confirm-text">
            {deleting !== null ? t('fields.deleteConfirm', { name: deleting.name }) : ''}
          </p>
          <div className="mesh-labels__dialog-footer">
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => void handleDelete()}
              isLoading={isDeleting}
              data-testid="field-delete-confirm"
            >
              {t('common.confirm')}
            </Button>
          </div>
        </div>
      </Dialog>

      {optionsFor !== null ? (
        <OptionsEditorDialog
          client={client}
          field={optionsFor}
          onClose={() => {
            setOptionsFor(null);
            refresh();
          }}
        />
      ) : null}
    </section>
  );
}

/** 枚举选项编辑器(§4.3 选项管理:增删改、配色、停用)。 */
function OptionsEditorDialog(props: {
  readonly client: MeshApiClient;
  readonly field: CustomFieldDef;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { client, field, onClose } = props;
  const t = useT();
  const { addToast } = useToast();
  const [newName, setNewName] = useState('');
  // mesh-data-color: 选项数据色板默认值(数据色非主题取色,theme.md §2.5 合法例外)
  const [newColor, setNewColor] = useState('#3e63dd');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAdd = async (): Promise<void> => {
    const name = newName.trim();
    if (name.length === 0) {
      setError(t('fields.errors.optionNameEmpty'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createOption(client, field.id, {
        name,
        color: isValidHexColor(newColor) ? newColor : null,
        position: field.options.length,
      });
      setNewName('');
      // 父面板在关闭时刷新;本对话框直接展示服务端最新 options 需重取字段——
      // 简化:关闭对话框,列表刷新带回最新 options。
      onClose();
    } catch (err) {
      setError(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'));
    } finally {
      setBusy(false);
    }
  };

  const handleToggleOption = async (optionId: string, isActive: boolean): Promise<void> => {
    try {
      await updateOption(client, field.id, optionId, { is_active: !isActive });
      onClose();
    } catch (err) {
      addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const handleRemoveOption = async (optionId: string): Promise<void> => {
    try {
      await deleteOption(client, field.id, optionId);
      onClose();
    } catch (err) {
      addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={t('fields.options.dialogTitle', { name: field.name })}
      closeLabel={t('common.close')}
    >
      <div className="mesh-labels__dialog-body" data-testid="options-editor">
        <ul className="mesh-labels__list">
          {field.options.map((option) => (
            <li key={option.id} className="mesh-labels__row" data-testid={'option-row-' + option.name}>
              {option.color !== null ? (
                <span
                  className="mesh-labels__dot"
                  style={{ backgroundColor: option.color }}
                  aria-hidden="true"
                />
              ) : null}
              <span className="mesh-labels__name">{option.name}</span>
              <span className="mesh-labels__scope">
                {option.is_active ? t('fields.statusActive') : t('fields.statusInactive')}
              </span>
              <span className="mesh-labels__actions">
                <IconButton
                  label={t('fields.options.toggleLabel', { name: option.name })}
                  size="sm"
                  variant="ghost"
                  data-testid={'option-toggle-' + option.name}
                  onClick={() => void handleToggleOption(option.id, option.is_active)}
                >
                  {option.is_active ? t('fields.deactivateGlyph') : t('fields.activateGlyph')}
                </IconButton>
                <IconButton
                  label={t('fields.options.removeLabel', { name: option.name })}
                  size="sm"
                  variant="danger"
                  data-testid={'option-remove-' + option.name}
                  onClick={() => void handleRemoveOption(option.id)}
                >
                  {t('labels.deleteGlyph')}
                </IconButton>
              </span>
            </li>
          ))}
        </ul>
        <Input
          label={t('fields.options.newNameLabel')}
          value={newName}
          data-testid="option-new-name"
          onChange={(event) => setNewName(event.target.value)}
        />
        <ColorPicker
          label={t('fields.options.newColorLabel')}
          hexInputLabel={t('labels.dialog.hexLabel')}
          value={newColor}
          onChange={setNewColor}
        />
        {error !== null ? (
          <p role="alert" data-testid="options-editor-error">
            {error}
          </p>
        ) : null}
        <div className="mesh-labels__dialog-footer">
          <Button
            onClick={() => void handleAdd()}
            isLoading={busy}
            data-testid="option-add-confirm"
          >
            {t('fields.options.addButton')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
