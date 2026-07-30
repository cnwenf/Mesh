import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { avatarHueIndex, avatarInitials, Avatar } from '../components/Avatar';

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
    expect(avatar.className).toMatch(/mesh-avatar--h\d/);
    expect(avatar).toHaveClass('mesh-avatar--32');
  });

  it('agent 用统一轮廓图标而非随机 emoji', () => {
    const { container } = render(<Avatar name="Builder" kind="agent" />);
    const avatar = screen.getByRole('img', { name: 'Builder' });
    expect(avatar).toHaveClass('mesh-avatar--agent');
    expect(container.querySelector('svg')).not.toBeNull();
  });

  it('有 src 时渲染图片(alt 留空,名称由旁侧文案承载)', () => {
    const { container } = render(<Avatar name="Ada" src="/avatar.png" />);
    const img = container.querySelector('img')!;
    expect(img).toHaveAttribute('src', '/avatar.png');
    expect(img).toHaveAttribute('alt', '');
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
