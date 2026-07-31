import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DataView } from '../patterns/DataView';

describe('DataView(页面模板:页头 + 工具条 + 主体 + 分页 + 批量条,§4.4)', () => {
  it('渲染唯一 h1 页头与主体内容', () => {
    render(
      <DataView title="工作项">
        <div data-testid="rows">行内容</div>
      </DataView>,
    );
    expect(screen.getByTestId('data-view')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: '工作项' })).toBeInTheDocument();
    expect(screen.getByTestId('rows')).toBeInTheDocument();
  });

  it('工具条/动作/描述槽渲染', () => {
    render(
      <DataView
        title="t"
        description="d"
        toolbar={<div data-testid="toolbar">筛选</div>}
        actions={<button type="button">新建</button>}
      >
        <span />
      </DataView>,
    );
    expect(screen.getByTestId('toolbar')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建' })).toBeInTheDocument();
    expect(screen.getByText('d')).toBeInTheDocument();
  });

  it('未提供工具条/分页/批量条时不渲染对应容器', () => {
    const { container } = render(
      <DataView title="t">
        <span />
      </DataView>,
    );
    expect(container.querySelector('.mesh-data-view__toolbar')).toBeNull();
    expect(container.querySelector('.mesh-data-view__footer')).toBeNull();
  });

  it('分页槽与批量条槽渲染', () => {
    render(
      <DataView
        title="t"
        footer={<button type="button">加载更多</button>}
        bulkBar={<div data-testid="bulk">批量</div>}
      >
        <span />
      </DataView>,
    );
    expect(screen.getByRole('button', { name: '加载更多' })).toBeInTheDocument();
    expect(screen.getByTestId('bulk')).toBeInTheDocument();
  });

  it('面包屑透传 PageHeader', () => {
    render(
      <DataView title="t" crumbs={[{ label: '项目', to: '/p' }, { label: 't' }]}>
        <span />
      </DataView>,
    );
    expect(screen.getByRole('link', { name: '项目' })).toHaveAttribute('href', '/p');
  });

  it('className 合并', () => {
    const { container } = render(
      <DataView title="t" className="issues-page">
        <span />
      </DataView>,
    );
    expect(container.querySelector('.mesh-data-view')).toHaveClass('issues-page');
  });
});
