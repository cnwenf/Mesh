import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PublicFlowShell } from '../components/PublicFlowShell';

describe('PublicFlowShell(design-quality §4.4 PublicFlow 模板)', () => {
  it('渲染品牌名、唯一 h1 标题与说明', () => {
    render(
      <PublicFlowShell brandLabel="Mesh" title="Sign in to Mesh" description="Welcome back">
        <p>body</p>
      </PublicFlowShell>,
    );
    expect(screen.getByText('Mesh')).toBeTruthy();
    const heading = screen.getByRole('heading', { level: 1, name: 'Sign in to Mesh' });
    expect(heading).toBeTruthy();
    expect(screen.getByText('Welcome back')).toBeTruthy();
    expect(screen.getByText('body')).toBeTruthy();
  });

  it('提供 brandHref 时品牌为可返回首页的链接', () => {
    render(
      <PublicFlowShell brandLabel="Mesh" brandHref="/" title="t">
        <p>x</p>
      </PublicFlowShell>,
    );
    const link = screen.getByRole('link', { name: 'Mesh' });
    expect(link.getAttribute('href')).toBe('/');
  });

  it('未提供 brandHref 时品牌为非交互文本(无链接角色)', () => {
    render(
      <PublicFlowShell brandLabel="Mesh" title="t">
        <p>x</p>
      </PublicFlowShell>,
    );
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText('Mesh')).toBeTruthy();
  });

  it('提供 footer 时渲染安全·帮助信息区', () => {
    render(
      <PublicFlowShell brandLabel="Mesh" title="t" footer={<span>security note</span>}>
        <p>x</p>
      </PublicFlowShell>,
    );
    expect(screen.getByText('security note')).toBeTruthy();
  });

  it('未提供 footer / description 时不渲染对应区域', () => {
    const { container } = render(
      <PublicFlowShell brandLabel="Mesh" title="t">
        <p>x</p>
      </PublicFlowShell>,
    );
    expect(container.querySelector('.mesh-public-flow__footer')).toBeNull();
    expect(container.querySelector('.mesh-public-flow__description')).toBeNull();
  });

  it('空字符串 description 不渲染说明段落', () => {
    const { container } = render(
      <PublicFlowShell brandLabel="Mesh" title="t" description="">
        <p>x</p>
      </PublicFlowShell>,
    );
    expect(container.querySelector('.mesh-public-flow__description')).toBeNull();
  });

  it('标题使用 title-1 排版工具类', () => {
    render(
      <PublicFlowShell brandLabel="Mesh" title="Heading">
        <p>x</p>
      </PublicFlowShell>,
    );
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading.className).toContain('mesh-text-title-1');
  });
});
