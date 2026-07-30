/**
 * Switch 契约测试(design-quality §9.1:role=switch + aria-checked)。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Switch } from '../components/Switch';

describe('Switch(二态开关)', () => {
  it('role=switch,aria-checked 反映受控态,点击上抛下一态', async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Switch label="仅提及我" checked={false} onCheckedChange={onCheckedChange} />);
    const control = screen.getByRole('switch', { name: '仅提及我' });
    expect(control).toHaveAttribute('aria-checked', 'false');
    await user.click(control);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it('选中态 aria-checked=true 且视觉类名切换', () => {
    render(<Switch label="开关" checked onCheckedChange={() => undefined} />);
    const control = screen.getByRole('switch');
    expect(control).toHaveAttribute('aria-checked', 'true');
    expect(control.className).toContain('mesh-switch__control--on');
  });

  it('disabled 时不可切换', async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Switch label="锁定" checked={false} onCheckedChange={onCheckedChange} disabled />);
    const control = screen.getByRole('switch');
    expect(control).toBeDisabled();
    await user.click(control);
    expect(onCheckedChange).not.toHaveBeenCalled();
  });

  it('description 经 aria-describedby 关联(可选)', () => {
    render(<Switch label="开关" description="全员可见" checked onCheckedChange={() => undefined} />);
    const control = screen.getByRole('switch');
    const described = control.getAttribute('aria-describedby');
    expect(described).not.toBeNull();
    expect(document.getElementById(described ?? '')).toHaveTextContent('全员可见');
  });

  it('无 description 时不挂 describedby;id 前缀可注入', () => {
    render(<Switch label="开关" checked={false} onCheckedChange={() => undefined} id="demo" />);
    expect(screen.getByRole('switch')).not.toHaveAttribute('aria-describedby');
    expect(screen.getByRole('switch').id).toBe('demo');
  });
});
