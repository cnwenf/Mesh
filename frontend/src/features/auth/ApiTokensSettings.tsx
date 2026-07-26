/**
 * 设置 → API Tokens(auth.md §4.3 / §3.2):创建(明文仅一次)、列表
 * (prefix + 掩码,绝不展示明文/哈希)、撤销(即时失效)。工作区上下文。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api/client';
import { createToken, listTokens, revokeToken } from '../../api';
import type { ApiTokenInfo, CreatedApiToken } from '../../api';
import { Button, Input } from '../../design';
import { useT } from '../../i18n';

export interface ApiTokensSettingsProps {
  client: MeshApiClient;
  workspaceId: string;
}

export function ApiTokensSettings(props: ApiTokensSettingsProps): React.JSX.Element {
  const { client, workspaceId } = props;
  const t = useT();

  const [tokens, setTokens] = useState<ApiTokenInfo[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState('');
  const [created, setCreated] = useState<CreatedApiToken | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(() => {
    void listTokens(client, workspaceId)
      .then(setTokens)
      .catch(() => setTokens([]));
  }, [client, workspaceId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const handleCreate = async (): Promise<void> => {
    setNotice(null);
    const scopeList = scopes
      .split(',')
      .map((scope) => scope.trim())
      .filter((scope) => scope.length > 0);
    try {
      const result = await createToken(client, workspaceId, {
        name: name.trim(),
        scopes: scopeList,
      });
      setCreated(result);
      setNotice(t('tokens.created'));
      setName('');
      setScopes('');
      setCreating(false);
      reload();
    } catch {
      setNotice(t('common.unknownError'));
    }
  };

  const handleRevoke = async (tokenId: string): Promise<void> => {
    setNotice(null);
    try {
      await revokeToken(client, workspaceId, tokenId);
      setNotice(t('tokens.revoked'));
      reload();
    } catch {
      setNotice(t('common.unknownError'));
    }
  };

  return (
    <div className="mesh-settings__group">
      <h3 className="mesh-settings__heading">{t('tokens.title')}</h3>
      <p className="mesh-settings__hint">{t('tokens.description')}</p>
      {notice !== null ? (
        <p role="status" data-testid="tokens-notice">
          {notice}
        </p>
      ) : null}

      {created !== null ? (
        <div className="mesh-tokens__created" data-testid="token-created">
          <p>{t('tokens.created')}</p>
          <code data-testid="token-plaintext">{created.token}</code>
          <Button variant="secondary" onClick={() => setCreated(null)}>
            {t('common.close')}
          </Button>
        </div>
      ) : null}

      {creating ? (
        <div className="mesh-tokens__create" data-testid="token-create-form">
          <Input
            data-testid="token-name"
            label={t('tokens.nameLabel')}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            data-testid="token-scopes"
            label={t('tokens.scopesLabel')}
            value={scopes}
            onChange={(event) => setScopes(event.target.value)}
          />
          <Button data-testid="token-create-submit" onClick={() => void handleCreate()}>
            {t('tokens.createSubmit')}
          </Button>
        </div>
      ) : (
        <Button data-testid="token-create" onClick={() => setCreating(true)}>
          {t('tokens.create')}
        </Button>
      )}

      {tokens.length === 0 ? (
        <p data-testid="tokens-empty">{t('tokens.empty')}</p>
      ) : (
        <ul className="mesh-tokens__list">
          {tokens.map((token) => (
            <li key={token.id} data-testid={`token-${token.id}`}>
              <span data-testid={`token-name-${token.id}`}>{token.name}</span>
              <code>
                {token.prefix}…
              </code>
              <span>
                {token.scopes.length > 0 ? token.scopes.join(', ') : t('tokens.allScopes')}
              </span>
              <span>
                {t('tokens.expires')}:{' '}
                {token.expires_at ?? t('tokens.never')}
              </span>
              <Button
                variant="secondary"
                data-testid={`token-revoke-${token.id}`}
                onClick={() => void handleRevoke(token.id)}
              >
                {t('tokens.revoke')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
