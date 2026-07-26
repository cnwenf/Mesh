/**
 * 工作区切换器(workspace.md §4.2:左上角下拉,列出所有工作区,顶部「创建工作区」)。
 *
 * Dialog 呈现列表(名称/slug/我的角色);点击切换 → 导航 /w/{slug};
 * 创建入口打开 CreateWorkspaceWizard。工作区上下文内按钮显示当前工作区名。
 */
import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router';
import type { MeshApiClient } from '../api/client';
import { getApiClient } from '../api/instance';
import { fetchAllWorkspaceSummaries } from '../api/workspace';
import type { WorkspaceSummary } from '../api/workspace';
import { Button, Dialog } from '../design';
import { useT } from '../i18n';
import { CreateWorkspaceWizard } from './CreateWorkspaceWizard';
import { useOptionalWorkspace } from './WorkspaceProvider';

export interface WorkspaceSwitcherProps {
  client?: MeshApiClient;
}

export function WorkspaceSwitcher(props: WorkspaceSwitcherProps): React.JSX.Element {
  const client = props.client ?? getApiClient();
  const t = useT();
  const navigate = useNavigate();
  const workspaceContext = useOptionalWorkspace();

  const [open, setOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [workspaces, setWorkspaces] = useState<readonly WorkspaceSummary[] | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const openSwitcher = useCallback((): void => {
    setOpen(true);
    setIsLoading(true);
    setLoadFailed(false);
    fetchAllWorkspaceSummaries(client)
      .then((summaries) => {
        setWorkspaces(summaries);
        setIsLoading(false);
      })
      .catch(() => {
        setWorkspaces(null);
        setIsLoading(false);
        setLoadFailed(true);
      });
  }, [client]);

  const switchTo = (slug: string): void => {
    setOpen(false);
    navigate(`/w/${slug}`);
  };

  const currentSlug = workspaceContext?.workspace?.slug ?? null;
  const triggerLabel =
    workspaceContext?.workspace !== null && workspaceContext?.workspace !== undefined
      ? workspaceContext.workspace.name
      : t('workspace.switcher.label');

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        data-testid="ws-switcher-button"
        onClick={openSwitcher}
      >
        {triggerLabel}
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title={t('workspace.switcher.title')}
        closeLabel={t('a11y.closeDialog')}
      >
        <div className="mesh-switcher" data-testid="ws-switcher-dialog">
          <Button
            data-testid="ws-switcher-create"
            onClick={() => {
              setOpen(false);
              setWizardOpen(true);
            }}
          >
            {t('workspace.switcher.create')}
          </Button>
          {isLoading ? (
            <p role="status" data-testid="ws-switcher-loading">
              {t('common.loading')}
            </p>
          ) : null}
          {!isLoading && loadFailed ? (
            <p role="alert" data-testid="ws-switcher-error">
              {t('state.errorDescription')}
            </p>
          ) : null}
          {!isLoading && workspaces !== null && workspaces.length === 0 ? (
            <p data-testid="ws-switcher-empty">{t('workspace.switcher.empty')}</p>
          ) : null}
          {!isLoading && workspaces !== null && workspaces.length > 0 ? (
            <ul className="mesh-switcher__list">
              {workspaces.map((workspace) => (
                <li key={workspace.id} className="mesh-switcher__item">
                  <button
                    type="button"
                    data-testid={`ws-switcher-item-${workspace.slug}`}
                    aria-current={workspace.slug === currentSlug ? 'true' : undefined}
                    onClick={() => switchTo(workspace.slug)}
                  >
                    <strong>{workspace.name}</strong>
                    <span className="mesh-switcher__slug">/{workspace.slug}</span>
                    <span className="mesh-switcher__role">
                      {t(`roles.${workspace.my_role}`)}
                    </span>
                    {workspace.slug === currentSlug ? (
                      <span data-testid="ws-switcher-current">
                        {t('workspace.switcher.current')}
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </Dialog>
      <CreateWorkspaceWizard
        client={client}
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
      />
    </>
  );
}
