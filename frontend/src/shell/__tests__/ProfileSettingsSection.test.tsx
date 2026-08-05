import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { ProfileSettingsSection } from '../pages/settings/ProfileSettingsSection';
import { isSecureAvatarUrl } from '../pages/settings/profileValidation';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ME = {
  user: {
    id: 'u-1',
    email: 'jane@example.com',
    display_name: 'Jane',
    avatar_url: 'https://cdn.example/avatar.png',
    timezone: 'Asia/Shanghai',
  },
  memberships: [],
};

function deferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe('ProfileSettingsSection', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('loads the canonical current-user profile and renders editable identity fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ data: ME })),
    );
    renderWithProviders(<ProfileSettingsSection />);

    expect(await screen.findByDisplayValue('Jane')).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://cdn.example/avatar.png')).toBeInTheDocument();
    expect(screen.getByText('jane@example.com')).toBeInTheDocument();
    expect(
      document.querySelector('.mesh-settings-section__identity-preview .mesh-avatar'),
    ).toBeInTheDocument();
  });

  it('autosaves display name on blur through PATCH /api/v1/users/me', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return jsonResponse({ data: { ...ME.user, display_name: 'Jane Doe' } });
      }
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const name = await screen.findByDisplayValue('Jane');
    fireEvent.change(name, { target: { value: 'Jane Doe' } });
    fireEvent.blur(name);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/users/me'),
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({ display_name: 'Jane Doe' }),
        }),
      ),
    );
    expect(await screen.findByText('Profile saved.')).toBeInTheDocument();
  });

  it('validates avatar URLs before writing and exposes a retryable load error', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ data: ME }));
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByDisplayValue('https://cdn.example/avatar.png');
    fireEvent.change(avatar, { target: { value: 'http://insecure.example/avatar.png' } });
    fireEvent.blur(avatar);
    expect(await screen.findByText('Use an HTTPS avatar URL.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('never passes an unsafe avatar draft to the image preview', async () => {
    const imageSources: string[] = [];
    class ImageStub {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      complete = false;
      naturalWidth = 0;

      set src(value: string) {
        imageSources.push(value);
      }
    }
    vi.stubGlobal('Image', ImageStub);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ data: ME })),
    );
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    expect(imageSources).toContain('https://cdn.example/avatar.png');

    fireEvent.change(avatar, { target: { value: 'data:image/svg+xml,<svg></svg>' } });

    expect(imageSources).not.toContain('data:image/svg+xml,<svg></svg>');
  });

  it('shows a retryable load error and recovers through the same current-user endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(jsonResponse({ data: ME }));
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByDisplayValue('Jane')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('rejects an empty display name without PATCH and surfaces write failures inline', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') throw new Error('save failed');
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const name = await screen.findByDisplayValue('Jane');
    fireEvent.change(name, { target: { value: '   ' } });
    fireEvent.blur(name);
    expect(
      await screen.findByText('Enter a name between 1 and 80 characters.'),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.change(name, { target: { value: 'Jane Doe' } });
    fireEvent.blur(name);
    expect(await screen.findByText('Could not save your profile. Try again.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('writes a changed HTTPS avatar and leaves unchanged values as no-ops', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return jsonResponse({
          data: { ...ME.user, avatar_url: 'https://cdn.example/new-avatar.png' },
        });
      }
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const name = await screen.findByDisplayValue('Jane');
    fireEvent.blur(name);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const avatar = screen.getByDisplayValue('https://cdn.example/avatar.png');
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/new-avatar.png' } });
    fireEvent.blur(avatar);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining('/api/v1/users/me'),
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ avatar_url: 'https://cdn.example/new-avatar.png' }),
      }),
    );
    expect(await screen.findByText('Profile saved.')).toBeInTheDocument();
  });

  it('leaves an unchanged avatar URL as a no-op', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ data: ME }));
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByDisplayValue('https://cdn.example/avatar.png');
    fireEvent.blur(avatar);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('accepts the saved profile as authoritative when the server clears a submitted avatar', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return jsonResponse({ data: { ...ME.user, avatar_url: null } });
      }
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/replaced.png' } });
    fireEvent.blur(avatar);

    await screen.findByText('Profile saved.');
    expect(avatar).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'Restore default avatar' })).toBeNull();
  });

  it('clears the avatar through the UI and restores the generated default', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return jsonResponse({ data: { ...ME.user, avatar_url: null } });
      }
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    await screen.findByDisplayValue('https://cdn.example/avatar.png');
    fireEvent.click(screen.getByRole('button', { name: 'Restore default avatar' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith(
        expect.stringContaining('/api/v1/users/me'),
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({ avatar_url: null }),
        }),
      ),
    );
    expect(screen.getByLabelText('Avatar URL')).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'Restore default avatar' })).toBeNull();
  });

  it('does not autosave a focused avatar draft before restoring the default', async () => {
    const patchBodies: unknown[] = [];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        patchBodies.push(JSON.parse(String(init.body)));
        return jsonResponse({ data: { ...ME.user, avatar_url: null } });
      }
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    await user.clear(avatar);
    await user.type(avatar, 'https://cdn.example/unsaved-draft.png');
    await user.click(screen.getByRole('button', { name: 'Restore default avatar' }));

    await waitFor(() => expect(patchBodies).toHaveLength(1));
    expect(patchBodies).toEqual([{ avatar_url: null }]);
    expect(avatar).toHaveValue('');
  });

  it('serializes a keyboard-triggered clear behind the avatar blur save', async () => {
    const draftSave = deferred<Response>();
    const clearSave = deferred<Response>();
    const pending = [draftSave, clearSave];
    const patchBodies: unknown[] = [];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method !== 'PATCH') return Promise.resolve(jsonResponse({ data: ME }));
      patchBodies.push(JSON.parse(String(init.body)));
      return pending.shift()!.promise;
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    await user.clear(avatar);
    await user.type(avatar, 'https://cdn.example/keyboard-draft.png');
    await user.tab();
    expect(screen.getByRole('button', { name: 'Restore default avatar' })).toHaveFocus();
    await user.keyboard('{Enter}');

    expect(patchBodies).toEqual([{ avatar_url: 'https://cdn.example/keyboard-draft.png' }]);
    await act(async () => {
      draftSave.resolve(
        jsonResponse({
          data: { ...ME.user, avatar_url: 'https://cdn.example/keyboard-draft.png' },
        }),
      );
      await draftSave.promise;
    });
    await waitFor(() => expect(patchBodies).toHaveLength(2));
    expect(patchBodies[1]).toEqual({ avatar_url: null });

    await act(async () => {
      clearSave.resolve(jsonResponse({ data: { ...ME.user, avatar_url: null } }));
      await clearSave.promise;
    });
    expect(avatar).toHaveValue('');
  });

  it('rolls a failed queued clear back to the preceding authoritative avatar save', async () => {
    const draftSave = deferred<Response>();
    const clearSave = deferred<Response>();
    const pending = [draftSave, clearSave];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === 'PATCH'
        ? pending.shift()!.promise
        : Promise.resolve(jsonResponse({ data: ME })),
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    await user.clear(avatar);
    await user.type(avatar, 'https://cdn.example/client-draft.png');
    await user.tab();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await user.keyboard('{Enter}');
    expect(avatar).toHaveValue('');

    await act(async () => {
      draftSave.resolve(
        jsonResponse({
          data: { ...ME.user, avatar_url: 'https://cdn.example/server-normalized.png' },
        }),
      );
      await draftSave.promise;
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    await act(async () => {
      clearSave.resolve(
        jsonResponse(
          { error: { code: 'internal_error', message: 'clear failed', details: {} } },
          500,
        ),
      );
      await clearSave.promise;
    });

    expect(await screen.findByText('Could not save your profile. Try again.')).toBeInTheDocument();
    expect(avatar).toHaveValue('https://cdn.example/server-normalized.png');
  });

  it('rolls an optimistic avatar clear back when the server rejects it', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') throw new Error('save failed');
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    await screen.findByDisplayValue('https://cdn.example/avatar.png');
    fireEvent.click(screen.getByRole('button', { name: 'Restore default avatar' }));

    expect(await screen.findByText('Could not save your profile. Try again.')).toBeInTheDocument();
    expect(screen.getByLabelText('Avatar URL')).toHaveValue('https://cdn.example/avatar.png');
    expect(screen.getByRole('button', { name: 'Restore default avatar' })).toBeInTheDocument();
  });

  it('clears an unsaved avatar draft when the server profile has no avatar', async () => {
    const clearSave = deferred<Response>();
    const withoutAvatar = { ...ME, user: { ...ME.user, avatar_url: null } };
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === 'PATCH'
        ? clearSave.promise
        : Promise.resolve(jsonResponse({ data: withoutAvatar })),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/draft.png' } });
    fireEvent.click(screen.getByRole('button', { name: 'Restore default avatar' }));
    expect(avatar).toHaveValue('');
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    fireEvent.change(avatar, { target: { value: 'https://cdn.example/new-draft.png' } });
    await act(async () => {
      clearSave.resolve(jsonResponse({ data: withoutAvatar.user }));
      await clearSave.promise;
    });
    expect(avatar).toHaveValue('https://cdn.example/new-draft.png');
  });

  it('keeps a newer unsaved avatar edit when an earlier clear fails', async () => {
    const clearSave = deferred<Response>();
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === 'PATCH' ? clearSave.promise : Promise.resolve(jsonResponse({ data: ME })),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    fireEvent.click(screen.getByRole('button', { name: 'Restore default avatar' }));
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/keep-draft.png' } });

    await act(async () => {
      clearSave.resolve(
        jsonResponse(
          { error: { code: 'internal_error', message: 'save failed', details: {} } },
          500,
        ),
      );
      await clearSave.promise;
    });
    expect(await screen.findByText('Could not save your profile. Try again.')).toBeInTheDocument();
    expect(avatar).toHaveValue('https://cdn.example/keep-draft.png');
  });

  it('ignores a stale clear rejection after a newer avatar save starts', async () => {
    const clearSave = deferred<Response>();
    const avatarSave = deferred<Response>();
    const pending = [clearSave, avatarSave];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === 'PATCH'
        ? pending.shift()!.promise
        : Promise.resolve(jsonResponse({ data: ME })),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    fireEvent.click(screen.getByRole('button', { name: 'Restore default avatar' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/newer.png' } });
    fireEvent.blur(avatar);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      clearSave.resolve(
        jsonResponse(
          { error: { code: 'internal_error', message: 'save failed', details: {} } },
          500,
        ),
      );
      await clearSave.promise;
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(screen.queryByText('Could not save your profile. Try again.')).toBeNull();

    await act(async () => {
      avatarSave.resolve(
        jsonResponse({ data: { ...ME.user, avatar_url: 'https://cdn.example/newer.png' } }),
      );
      await avatarSave.promise;
    });
    expect(avatar).toHaveValue('https://cdn.example/newer.png');
  });

  it('uses a server validation rejection as the authority for an avatar write', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return jsonResponse(
          {
            error: {
              code: 'validation_error',
              message: 'avatar_url must be an https URL',
              details: { avatar_url: 'https://cdn.example/new.png' },
            },
          },
          400,
        );
      }
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/new.png' } });
    fireEvent.blur(avatar);

    expect(await screen.findByText('Use an HTTPS avatar URL.')).toBeInTheDocument();
  });

  it('keeps concurrent field saves isolated when responses arrive out of order', async () => {
    const nameSave = deferred<Response>();
    const avatarSave = deferred<Response>();
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method !== 'PATCH') return Promise.resolve(jsonResponse({ data: ME }));
      const body = JSON.parse(String(init.body)) as Record<string, string>;
      return 'display_name' in body ? nameSave.promise : avatarSave.promise;
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const name = await screen.findByDisplayValue('Jane');
    const avatar = screen.getByDisplayValue('https://cdn.example/avatar.png');
    fireEvent.change(name, { target: { value: 'Jane Doe' } });
    fireEvent.blur(name);
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/new-avatar.png' } });
    fireEvent.blur(avatar);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    await act(async () => {
      avatarSave.resolve(
        jsonResponse({
          data: { ...ME.user, avatar_url: 'https://cdn.example/new-avatar.png' },
        }),
      );
      await avatarSave.promise;
    });
    await act(async () => {
      nameSave.resolve(
        jsonResponse({
          data: { ...ME.user, display_name: 'Jane Doe' },
        }),
      );
      await nameSave.promise;
    });

    expect(screen.getByLabelText('Name')).toHaveValue('Jane Doe');
    expect(screen.getByLabelText('Avatar URL')).toHaveValue('https://cdn.example/new-avatar.png');
  });

  it('ignores stale name responses and preserves text edited while the latest save is pending', async () => {
    const firstSave = deferred<Response>();
    const secondSave = deferred<Response>();
    const pending = [firstSave, secondSave];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method !== 'PATCH') return Promise.resolve(jsonResponse({ data: ME }));
      return pending.shift()!.promise;
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const name = await screen.findByDisplayValue('Jane');
    fireEvent.change(name, { target: { value: 'First value' } });
    fireEvent.blur(name);
    fireEvent.change(name, { target: { value: 'Second value' } });
    fireEvent.blur(name);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    await act(async () => {
      firstSave.resolve(jsonResponse({ data: { ...ME.user, display_name: 'First value' } }));
      await firstSave.promise;
    });
    expect(name).toHaveValue('Second value');

    fireEvent.change(name, { target: { value: 'Still editing' } });
    await act(async () => {
      secondSave.resolve(jsonResponse({ data: { ...ME.user, display_name: 'Second value' } }));
      await secondSave.promise;
    });
    expect(name).toHaveValue('Still editing');
  });

  it('serializes avatar saves and preserves a newer unsaved URL', async () => {
    const firstSave = deferred<Response>();
    const secondSave = deferred<Response>();
    const pending = [firstSave, secondSave];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method !== 'PATCH') return Promise.resolve(jsonResponse({ data: ME }));
      return pending.shift()!.promise;
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/first.png' } });
    fireEvent.blur(avatar);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/second.png' } });
    fireEvent.blur(avatar);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      firstSave.resolve(
        jsonResponse({ data: { ...ME.user, avatar_url: 'https://cdn.example/first.png' } }),
      );
      await firstSave.promise;
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(avatar).toHaveValue('https://cdn.example/second.png');

    fireEvent.change(avatar, { target: { value: 'https://cdn.example/still-editing.png' } });
    await act(async () => {
      secondSave.resolve(
        jsonResponse({ data: { ...ME.user, avatar_url: 'https://cdn.example/second.png' } }),
      );
      await secondSave.promise;
    });
    expect(avatar).toHaveValue('https://cdn.example/still-editing.png');
  });

  it('surfaces avatar write failures without losing the entered URL', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') throw new Error('save failed');
      return jsonResponse({ data: ME });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/new.png' } });
    fireEvent.blur(avatar);

    expect(await screen.findByText('Could not save your profile. Try again.')).toBeInTheDocument();
    expect(avatar).toHaveValue('https://cdn.example/new.png');
  });

  it('ignores a profile response that settles after unmount', async () => {
    const load = deferred<Response>();
    vi.stubGlobal(
      'fetch',
      vi.fn(() => load.promise),
    );
    const view = renderWithProviders(<ProfileSettingsSection />);

    view.unmount();
    await act(async () => {
      load.resolve(jsonResponse({ data: ME }));
      await load.promise;
    });
  });

  it('supports profiles without an existing avatar URL', async () => {
    const withoutAvatar = { ...ME, user: { ...ME.user, avatar_url: undefined } };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return jsonResponse({
          data: { ...withoutAvatar.user, avatar_url: 'https://cdn.example/first-avatar.png' },
        });
      }
      return jsonResponse({ data: withoutAvatar });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithProviders(<ProfileSettingsSection />);

    const avatar = await screen.findByLabelText('Avatar URL');
    expect(avatar).toHaveValue('');
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/first-avatar.png' } });
    fireEvent.blur(avatar);
    expect(await screen.findByText('Profile saved.')).toBeInTheDocument();
  });
});

describe('isSecureAvatarUrl', () => {
  it.each([
    ['https://cdn.example/avatar.png', true],
    ['http://cdn.example/avatar.png', false],
    ['https://', false],
    ['https:///avatar.png', false],
    ['https://exa mple/avatar.png', false],
    ['https://cdn.example\\avatar.png', false],
    ['https://%', false],
    ['', false],
    ['not a URL', false],
  ])('validates %s', (value, expected) => {
    expect(isSecureAvatarUrl(value)).toBe(expected);
  });
});
