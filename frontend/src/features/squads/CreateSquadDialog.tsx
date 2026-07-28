/**
 * 新建小队对话框(squad.md §4.3-1):name / description / instructions / avatar_url /
 * kind / leader_mode / require_plan_approval / max_decompose_depth + 成员选择器。
 * 成员从工作区名册选取,各带角色(leader / member / observer);leader 闸门 ——
 * 至少一名组长方可创建(spec:「至少一名组长否则创建置灰」)。members 随 createSquad 上行。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Dialog, Input, Select } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { listMembers } from '../members/api';
import type { MemberSummary, Membership } from '../members/types';
import { createSquad } from './api';
import type { LeaderMode, Squad, SquadKind, SquadMemberInput, SquadRole } from './types';
import { SQUAD_KIND_ORDER, SQUAD_ROLE_ORDER } from './types';

const ROSTER_LIMIT = 100;
const MIN_DECOMPOSE_DEPTH = 1;
const MAX_DECOMPOSE_DEPTH = 4;
const DEFAULT_DECOMPOSE_DEPTH = 2;

/** 已选成员(本地态:含显示名供渲染;上行仅 member_id / role / member_type)。 */
interface PickedMember {
  readonly member_id: string;
  readonly name: string;
  readonly member_type: MemberSummary['member_type'];
  readonly role: SquadRole;
}

const LEADER_MODES: readonly LeaderMode[] = ['single', 'multi'];

export interface CreateSquadDialogProps {
  readonly workspace: Membership;
  readonly onCreated: (squad: Squad) => void;
  readonly onClose: () => void;
}

export function CreateSquadDialog(props: CreateSquadDialogProps): React.JSX.Element {
  const t = useT();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [avatarUrl, setAvatarUrl] = useState('');
  const [kind, setKind] = useState<SquadKind>('standing');
  const [leaderMode, setLeaderMode] = useState<LeaderMode>('single');
  const [requirePlanApproval, setRequirePlanApproval] = useState(false);
  const [maxDepth, setMaxDepth] = useState(DEFAULT_DECOMPOSE_DEPTH);
  const [roster, setRoster] = useState<MemberSummary[]>([]);
  const [picked, setPicked] = useState<PickedMember[]>([]);
  const [pickMemberId, setPickMemberId] = useState('');
  const [pickRole, setPickRole] = useState<SquadRole>('member');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const page = await listMembers(client, props.workspace.workspace_id, {
          limit: ROSTER_LIMIT,
        });
        if (!cancelled) setRoster([...page.data]);
      } catch {
        // 名册拉取失败:成员选择器为空,不阻断其余字段填写。
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, props.workspace.workspace_id]);

  const addPicked = useCallback(() => {
    if (pickMemberId === '') return;
    const member = roster.find((m) => m.id === pickMemberId);
    if (member === undefined) return;
    setPicked((prev) =>
      prev.some((p) => p.member_id === pickMemberId)
        ? prev
        : [
            ...prev,
            { member_id: member.id, name: member.display_name, member_type: member.member_type, role: pickRole },
          ],
    );
    setPickMemberId('');
    setPickRole('member');
  }, [pickMemberId, pickRole, roster]);

  const setPickedRole = useCallback((memberId: string, role: SquadRole) => {
    setPicked((prev) => prev.map((p) => (p.member_id === memberId ? { ...p, role } : p)));
  }, []);

  const removePicked = useCallback((memberId: string) => {
    setPicked((prev) => prev.filter((p) => p.member_id !== memberId));
  }, []);

  const hasLeader = picked.some((p) => p.role === 'leader');
  const canCreate = name.trim() !== '' && hasLeader;

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (!canCreate) return;
      setIsSaving(true);
      setError(null);
      const members: SquadMemberInput[] = picked.map((p) => ({
        member_id: p.member_id,
        role: p.role,
        member_type: p.member_type,
      }));
      try {
        const created = await createSquad(client, props.workspace.workspace_id, {
          name: name.trim(),
          description: description.trim() === '' ? undefined : description.trim(),
          instructions: instructions.trim() === '' ? undefined : instructions.trim(),
          avatar_url: avatarUrl.trim() === '' ? undefined : avatarUrl.trim(),
          kind,
          leader_mode: leaderMode,
          require_plan_approval: requirePlanApproval,
          max_decompose_depth: maxDepth,
          members,
        });
        props.onCreated(created);
        props.onClose();
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      } finally {
        setIsSaving(false);
      }
    },
    [
      canCreate,
      client,
      props,
      picked,
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
  const pickedIds = new Set(picked.map((p) => p.member_id));
  const candidates = roster.filter((m) => !pickedIds.has(m.id));

  return (
    <Dialog open onClose={props.onClose} title={t('squads.create')} closeLabel={t('common.close')}>
      <form
        className="mesh-squads__form"
        data-testid="squad-create-form"
        onSubmit={(event) => void submit(event)}
      >
        <Input
          label={t('squads.name')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t('squads.namePlaceholder')}
          data-testid="squad-create-name"
          autoFocus
        />
        <Input
          label={t('squads.description')}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder={t('squads.descriptionPlaceholder')}
          data-testid="squad-create-description"
        />
        <label className="mesh-squads__field">
          <span>{t('squads.instructions')}</span>
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            placeholder={t('squads.instructionsPlaceholder')}
            data-testid="squad-create-instructions"
            rows={3}
          />
        </label>
        <Input
          label={t('squads.avatarUrl')}
          value={avatarUrl}
          onChange={(event) => setAvatarUrl(event.target.value)}
          placeholder={t('squads.avatarUrlPlaceholder')}
          data-testid="squad-create-avatar"
        />
        <Select
          label={t('squads.kind')}
          value={kind}
          data-testid="squad-create-kind"
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
          data-testid="squad-create-leader-mode"
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
            data-testid="squad-create-require-approval"
          />
          {t('squads.requirePlanApproval')}
        </label>
        <Select
          label={t('squads.maxDecomposeDepth')}
          value={String(maxDepth)}
          data-testid="squad-create-depth"
          onChange={(event) => setMaxDepth(Number.parseInt(event.target.value, 10))}
        >
          {depthOptions.map((depth) => (
            <option key={depth} value={String(depth)}>
              {String(depth)}
            </option>
          ))}
        </Select>

        <fieldset className="mesh-squads__member-picker">
          <legend>{t('squads.create.members')}</legend>
          <div className="mesh-squads__member-picker-row">
            <Select
              label={t('squads.detail.selectMember')}
              value={pickMemberId}
              data-testid="squad-create-member-select"
              onChange={(event) => setPickMemberId(event.target.value)}
            >
              <option value="">{t('squads.detail.selectMember')}</option>
              {candidates.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </Select>
            <Select
              label={t('squads.detail.role')}
              value={pickRole}
              data-testid="squad-create-member-role"
              onChange={(event) => setPickRole(event.target.value as SquadRole)}
            >
              {SQUAD_ROLE_ORDER.map((role) => (
                <option key={role} value={role}>
                  {t(`squads.role.${role}`)}
                </option>
              ))}
            </Select>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={addPicked}
              disabled={pickMemberId === ''}
              data-testid="squad-create-member-add"
            >
              {t('squads.detail.add')}
            </Button>
          </div>
          {picked.length === 0 ? (
            <p className="mesh-squads__pane-empty">{t('squads.create.noMembers')}</p>
          ) : (
            <ul className="mesh-squads__picked-members">
              {picked.map((member) => (
                <li key={member.member_id} data-testid={`squad-create-picked-${member.member_id}`}>
                  <span className="mesh-squads__picked-name">{member.name}</span>
                  <Select
                    label={t('squads.detail.role')}
                    value={member.role}
                    data-testid={`squad-create-picked-role-${member.member_id}`}
                    onChange={(event) => setPickedRole(member.member_id, event.target.value as SquadRole)}
                  >
                    {SQUAD_ROLE_ORDER.map((role) => (
                      <option key={role} value={role}>
                        {t(`squads.role.${role}`)}
                      </option>
                    ))}
                  </Select>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger"
                    onClick={() => removePicked(member.member_id)}
                    data-testid={`squad-create-picked-remove-${member.member_id}`}
                  >
                    {t('squads.detail.remove')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {!hasLeader ? (
            <p className="mesh-squads__leader-gate" data-testid="squad-create-leader-gate">
              {t('squads.create.leaderRequired')}
            </p>
          ) : null}
        </fieldset>

        {error !== null ? (
          <p className="mesh-squads__form-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="mesh-squads__form-actions">
          <Button type="submit" disabled={isSaving || !canCreate} data-testid="squad-create-submit">
            {t('common.create')}
          </Button>
          <Button type="button" variant="ghost" onClick={props.onClose}>
            {t('common.cancel')}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
