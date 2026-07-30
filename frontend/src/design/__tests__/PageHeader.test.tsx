/**
 * PageHeader / Toolbar 契约测试(design-quality §1.2/§4.4)。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PageHeader } from '../components/PageHeader';
import { Toolbar } from '../components/Toolbar';

describe('PageHeader(页面唯一主标题)', () => {
  it('渲染唯一 h1 与描述;eyebrow 与动作区为可选槽', () => {
    render(
      <PageHeader
        title="工作项"
        eyebrow={<nav aria-label="面包屑">项目 / 前端</nav>}
        description="全部开放工作项"
        actions={<button type="button">新建</button>}
      />,
    );
    const headings = screen.getAllByRole('heading', { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent('工作项');
    expect(screen.getByText('全部开放工作项')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '面包屑' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建' })).toBeInTheDocument();
  });

  it('仅 title 也可渲染(无 eyebrow/description/actions 分支)', () => {
    const { container } = render(<PageHeader title="收件箱" />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('收件箱');
    expect(container.querySelector('.mesh-page-header__eyebrow')).toBeNull();
    expect(container.querySelector('.mesh-page-header__actions')).toBeNull();
    expect(container.querySelector('.mesh-page-header__description')).toBeNull();
  });

  it('children 槽透传', () => {
    render(
      <PageHeader title="看板">
        <div data-testid="extra">扩展区</div>
      </PageHeader>,
    );
    expect(screen.getByTestId('extra')).toBeInTheDocument();
  });
});

describe('Toolbar(视图/筛选容器)', () => {
  it('role=toolbar 且 aria-label 必填生效;className 合并', () => {
    render(
      <Toolbar label="看板视图操作" className="extra">
        <button type="button">筛选</button>
      </Toolbar>,
    );
    const toolbar = screen.getByRole('toolbar', { name: '看板视图操作' });
    expect(toolbar.className).toContain('mesh-toolbar');
    expect(toolbar.className).toContain('extra');
  });
});
