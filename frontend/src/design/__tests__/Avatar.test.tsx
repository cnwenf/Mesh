import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { avatarHueIndex, avatarInitials, Avatar } from '../components/Avatar';

afterEach(() => vi.unstubAllGlobals());

describe('avatarInitials(§7.2 姓名缩写)', () => {
  it('拉丁双词名取两词首字母大写', () => {
    expect(avatarInitials('Ada Lovelace')).toBe('AL');
  });

  it('拉丁单词名取首字母大写', () => {
    expect(avatarInitials('plato')).toBe('P');
  });

  it('中文双字名取末两字', () => {
    expect(avatarInitials('李小龙')).toBe('小龙');
  });

  it('中文单字名取首字', () => {
    expect(avatarInitials('龙')).toBe('龙');
  });

  it('空名回退 ?', () => {
    expect(avatarInitials('   ')).toBe('?');
  });

  it('多空白词只取前两词', () => {
    expect(avatarInitials('  Grace   Hopper  Navy ')).toBe('GH');
  });
});

describe('avatarHueIndex(稳定 hash 取色)', () => {
  it('同名稳定、值域 0–7', () => {
    const first = avatarHueIndex('Mesh Agent');
    expect(first).toBe(avatarHueIndex('Mesh Agent'));
    for (const name of ['a', '张三', 'Runtime-01', 'very-long-name-with-many-parts']) {
      const index = avatarHueIndex(name);
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(8);
    }
  });

  it('不同名通常散列到不同桶(抽样不全相同)', () => {
    const buckets = new Set(
      ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l'].map((name) =>
        avatarHueIndex(name),
      ),
    );
    expect(buckets.size).toBeGreaterThan(1);
  });
});

describe('Avatar 组件', () => {
  it('人类默认渲染缩写 + hash 配色类', () => {
    render(<Avatar name="Ada Lovelace" />);
    const avatar = screen.getByRole('img', { name: 'Ada Lovelace' });
    expect(avatar).toHaveTextContent('AL');
    expect(avatar).toHaveAttribute('data-slot', 'avatar');
    expect(avatar.querySelector('[data-slot="avatar-fallback"]')).not.toBeNull();
    expect(avatar.className).toMatch(/mesh-avatar--h\d/);
    expect(avatar).toHaveClass('mesh-avatar--32');
  });

  it('agent 用统一轮廓图标而非随机 emoji', () => {
    const { container } = render(<Avatar name="Builder" kind="agent" />);
    const avatar = screen.getByRole('img', { name: 'Builder' });
    expect(avatar).toHaveClass('mesh-avatar--agent');
    expect(container.querySelector('svg')).not.toBeNull();
  });

  it('有 src 且加载成功时渲染图片(alt 留空,名称由旁侧文案承载)', async () => {
    class LoadedImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      complete = false;
      naturalWidth = 0;
      crossOrigin: string | null = null;
      referrerPolicy = '';
      sizes = '';
      srcset = '';

      set src(_value: string) {
        this.complete = true;
        this.naturalWidth = 32;
      }
    }
    vi.stubGlobal('Image', LoadedImage);
    const { container } = render(<Avatar name="Ada" src="/avatar.png" />);
    await waitFor(() => expect(container.querySelector('img')).not.toBeNull());
    const img = container.querySelector('img') as HTMLImageElement;
    expect(img).toHaveAttribute('src', '/avatar.png');
    expect(img).toHaveAttribute('alt', '');
    expect(img).toHaveAttribute('data-slot', 'avatar-image');
  });

  it('src 为空字符串时仍回退缩写', () => {
    render(<Avatar name="Ada Lovelace" src="" />);
    expect(screen.getByRole('img', { name: 'Ada Lovelace' })).toHaveTextContent('AL');
  });

  it('全部尺寸档与 className 透传', () => {
    const { rerender } = render(<Avatar name="a" size={20} />);
    expect(screen.getByRole('img', { name: 'a' })).toHaveClass('mesh-avatar--20');
    rerender(<Avatar name="a" size={56} className="custom" />);
    expect(screen.getByRole('img', { name: 'a' })).toHaveClass('mesh-avatar--56', 'custom');
  });

  it('大尺寸 agent 用 24px 图标', () => {
    const { container } = render(<Avatar name="Big Agent" kind="agent" size={56} />);
    expect(container.querySelector('svg')).toHaveAttribute('width', '24');
  });
});
