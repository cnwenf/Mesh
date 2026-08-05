import { act, fireEvent, screen, waitFor } from '@testing-library/react';
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

  it('ignores stale avatar responses and preserves a newer unsaved URL', async () => {
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
    fireEvent.change(avatar, { target: { value: 'https://cdn.example/second.png' } });
    fireEvent.blur(avatar);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    await act(async () => {
      firstSave.resolve(
        jsonResponse({ data: { ...ME.user, avatar_url: 'https://cdn.example/first.png' } }),
      );
      await firstSave.promise;
    });
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
    ['', false],
    ['not a URL', false],
  ])('validates %s', (value, expected) => {
    expect(isSecureAvatarUrl(value)).toBe(expected);
  });
});
