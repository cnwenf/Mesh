import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { IconButton } from '../components/IconButton';

describe('IconButton', () => {
  it('label(必填)成为可访问名(aria-label)', () => {
    render(<IconButton label="Close">×</IconButton>);
    const button = screen.getByRole('button', { name: 'Close' });
    expect(button).toHaveAttribute('aria-label', 'Close');
  });

  it('图标内容对读屏隐藏(aria-hidden),仅经 label 朗读', () => {
    const { container } = render(<IconButton label="Settings">⚙</IconButton>);
    const icon = container.querySelector('.mesh-icon-button__icon');
    expect(icon).not.toBeNull();
    expect(icon).toHaveAttribute('aria-hidden', 'true');
  });

  it('响应点击', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <IconButton label="Delete" onClick={onClick}>
        🗑
      </IconButton>,
    );
    await user.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('默认 variant=ghost、size=md;可覆盖', () => {
    render(<IconButton label="A">a</IconButton>);
    const button = screen.getByRole('button', { name: 'A' });
    expect(button.className).toContain('mesh-button--ghost');
    expect(button.className).toContain('mesh-icon-button');
    expect(button.className).toContain('mesh-icon-button--md');
  });

  it('转发 ref', () => {
    const ref = createRef<HTMLButtonElement>();
    render(
      <IconButton label="Ref" ref={ref}>
        r
      </IconButton>,
    );
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
  });
});
