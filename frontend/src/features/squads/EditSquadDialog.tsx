/**
 * 编辑小队对话框(squad.md §4.2):接线 updateSquad(此前 api 已备而未接)。
 * 可编辑 name / description / instructions / avatar_url / kind / leader_mode /
 * require_plan_approval / max_decompose_depth;成员增减仍走详情页成员面板。
 */
import { useCallback, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Dialog, Input, Select } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import type { Membership } from '../members/types';
import { updateSquad } from './api';
import type { LeaderMode, Squad, SquadKind } from './types';
import { SQUAD_KIND_ORDER } from './types';

const MIN_DECOMPOSE_DEPTH = 1;
const MAX_DECOMPOSE_DEPTH = 4;
const LEADER_MODES: readonly LeaderMode[] = ['single', 'multi'];

export interface EditSquadDialogProps {
  readonly workspace: Membership;
  readonly squad: Squad;
  readonly onSaved: (squad: Squad) => void;
  readonly onClose: () => void;
}

export function EditSquadDialog(props: EditSquadDialogProps): React.JSX.Element {
  const t = useT();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const { squad } = props;
  const [name, setName] = useState(squad.name);
  const [description, setDescription] = useState(squad.description ?? '');
  const [instructions, setInstructions] = useState(squad.instructions ?? '');
  const [avatarUrl, setAvatarUrl] = useState(squad.avatar_url ?? '');
  const [kind, setKind] = useState<SquadKind>(squad.kind);
  const [leaderMode, setLeaderMode] = useState<LeaderMode>(squad.leader_mode);
  const [requirePlanApproval, setRequirePlanApproval] = useState(squad.require_plan_approval);
  const [maxDepth, setMaxDepth] = useState(squad.max_decompose_depth);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (name.trim() === '') return;
      setIsSaving(true);
      setError(null);
      try {
        const updated = await updateSquad(client, props.workspace.workspace_id, squad.id, {
          name: name.trim(),
          description: description.trim() === '' ? null : description.trim(),
          instructions: instructions.trim() === '' ? null : instructions.trim(),
          avatar_url: avatarUrl.trim() === '' ? null : avatarUrl.trim(),
          kind,
          leader_mode: leaderMode,
          require_plan_approval: requirePlanApproval,
          max_decompose_depth: maxDepth,
        });
        props.onSaved(updated);
        props.onClose();
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      } finally {
        setIsSaving(false);
      }
    },
    [
      client,
      props,
      squad.id,
      name,
      description,
      instructions,
      avatarUrl,
      kind,
      leaderMode,
      requirePlanApproval,
      maxDepth,
      t,
    ],
  );

  const depthOptions = Array.from(
    { length: MAX_DECOMPOSE_DEPTH - MIN_DECOMPOSE_DEPTH + 1 },
    (_, offset) => MIN_DECOMPOSE_DEPTH + offset,
  );

  return (
    <Dialog open onClose={props.onClose} title={t('squads.edit')} closeLabel={t('common.close')}>
      <form
        className="mesh-squads__form"
        data-testid="squad-edit-form"
        onSubmit={(event) => void submit(event)}
      >
        <Input
          label={t('squads.name')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          data-testid="squad-edit-name"
          autoFocus
        />
        <Input
          label={t('squads.description')}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          data-testid="squad-edit-description"
        />
        <label className="mesh-squads__field">
          <span>{t('squads.instructions')}</span>
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            data-testid="squad-edit-instructions"
            rows={3}
          />
        </label>
        <Input
          label={t('squads.avatarUrl')}
          value={avatarUrl}
          onChange={(event) => setAvatarUrl(event.target.value)}
          data-testid="squad-edit-avatar"
        />
        <Select
          label={t('squads.kind')}
          value={kind}
          data-testid="squad-edit-kind"
          onChange={(event) => setKind(event.target.value as SquadKind)}
        >
          {SQUAD_KIND_ORDER.map((value) => (
            <option key={value} value={value}>
              {t(`squads.kind.${value}`)}
            </option>
          ))}
        </Select>
        <Select
          label={t('squads.leaderMode')}
          value={leaderMode}
          data-testid="squad-edit-leader-mode"
          onChange={(event) => setLeaderMode(event.target.value as LeaderMode)}
        >
          {LEADER_MODES.map((value) => (
            <option key={value} value={value}>
              {t(`squads.leaderMode.${value}`)}
            </option>
          ))}
        </Select>
        <label className="mesh-squads__checkbox">
          <input
            type="checkbox"
            checked={requirePlanApproval}
            onChange={(event) => setRequirePlanApproval(event.target.checked)}
            data-testid="squad-edit-require-approval"
          />
          {t('squads.requirePlanApproval')}
        </label>
        <Select
          label={t('squads.maxDecomposeDepth')}
          value={String(maxDepth)}
          data-testid="squad-edit-depth"
          onChange={(event) => setMaxDepth(Number.parseInt(event.target.value, 10))}
        >
          {depthOptions.map((depth) => (
            <option key={depth} value={String(depth)}>
              {String(depth)}
            </option>
          ))}
        </Select>
        {error !== null ? (
          <p className="mesh-squads__form-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="mesh-squads__form-actions">
          <Button type="submit" disabled={isSaving || name.trim() === ''} data-testid="squad-edit-submit">
            {t('common.save')}
          </Button>
          <Button type="button" variant="ghost" onClick={props.onClose}>
            {t('common.cancel')}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
