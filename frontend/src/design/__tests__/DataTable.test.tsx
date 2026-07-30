/**
 * DataTable 契约测试(design-quality §7.6/§10.2)。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DataTable } from '../components/DataTable';
import type { DataTableColumn } from '../components/DataTable';

interface Row {
  readonly id: string;
  readonly name: string;
  readonly count: number;
}

const ROWS: ReadonlyArray<Row> = [
  { id: 'a', name: '甲', count: 3 },
  { id: 'b', name: '乙', count: 12 },
];

const COLUMNS: ReadonlyArray<DataTableColumn<Row>> = [
  { id: 'name', header: '名称', cell: (row) => row.name, sortable: true },
  { id: 'count', header: '数量', cell: (row) => row.count, align: 'end' },
];

describe('DataTable(语义表格)', () => {
  it('caption + scope=col 表头 + 行单元格渲染', () => {
    render(<DataTable caption="成员名册" columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} />);
    expect(screen.getByRole('table', { name: '成员名册' })).toBeInTheDocument();
    const headers = screen.getAllByRole('columnheader');
    expect(headers).toHaveLength(2);
    expect(headers[0]).toHaveTextContent('名称');
    expect(screen.getAllByRole('row')).toHaveLength(3); // 表头 + 2 行
    expect(screen.getAllByRole('cell')).toHaveLength(4);
  });

  it('hideCaption 视觉隐藏但读屏可达', () => {
    const { container } = render(
      <DataTable caption="隐藏标题" hideCaption columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} />,
    );
    expect(screen.getByRole('table', { name: '隐藏标题' })).toBeInTheDocument();
    expect(container.querySelector('.mesh-visually-hidden')).not.toBeNull();
  });

  it('排序表头:aria-sort 随受控状态;点击上抛列 id', async () => {
    const user = userEvent.setup();
    const onSortChange = vi.fn();
    render(
      <DataTable
        caption="排序表"
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        sortBy={{ id: 'name', direction: 'asc' }}
        onSortChange={onSortChange}
      />,
    );
    const nameHeader = screen.getByRole('columnheader', { name: /名称/ });
    expect(nameHeader).toHaveAttribute('aria-sort', 'ascending');
    await user.click(screen.getByRole('button', { name: /名称/ }));
    expect(onSortChange).toHaveBeenCalledWith('name');
  });

  it('降序 aria-sort=descending;未排序列无 aria-sort 且无排序按钮', () => {
    render(
      <DataTable
        caption="排序表"
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        sortBy={{ id: 'name', direction: 'desc' }}
      />,
    );
    expect(screen.getByRole('columnheader', { name: /名称/ })).toHaveAttribute('aria-sort', 'descending');
    const countHeader = screen.getByRole('columnheader', { name: '数量' });
    expect(countHeader).not.toHaveAttribute('aria-sort');
    expect(countHeader.querySelector('button')).toBeNull();
  });

  it('行主操作:整行可点可聚焦,Enter 触发;rowClassName 合并', async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    const { container } = render(
      <DataTable
        caption="可点行"
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        onRowClick={onRowClick}
        rowClassName={(row) => (row.id === 'a' ? 'row-a' : undefined)}
        density="comfortable"
      />,
    );
    expect(container.querySelector('.mesh-data-table--comfortable')).not.toBeNull();
    expect(container.querySelector('.row-a')).not.toBeNull();
    const firstRow = screen.getAllByRole('row')[1];
    if (firstRow === undefined) throw new Error('缺少数据行');
    expect(firstRow).toHaveAttribute('tabindex', '0');
    await user.click(firstRow);
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);
    firstRow.focus();
    await user.keyboard('{Enter}');
    expect(onRowClick).toHaveBeenCalledTimes(2);
  });

  it('空数据渲染 emptyState 占满全列', () => {
    render(
      <DataTable
        caption="空表"
        columns={COLUMNS}
        rows={[]}
        rowKey={(row) => row.id}
        emptyState={<p>尚无成员</p>}
      />,
    );
    const emptyCell = screen.getByText('尚无成员').closest('td');
    expect(emptyCell).not.toBeNull();
    expect(emptyCell).toHaveAttribute('colspan', '2');
  });

  it('无 emptyState 的空表渲染空 tbody;无 onRowClick 的行不可聚焦', () => {
    const { container } = render(
      <DataTable caption="空表" columns={COLUMNS} rows={[]} rowKey={(row) => row.id} />,
    );
    expect(container.querySelector('tbody')).toBeEmptyDOMElement();
    const { container: withRows } = render(
      <DataTable caption="普通表" columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} />,
    );
    expect(withRows.querySelector('.mesh-data-table__row')).not.toHaveAttribute('tabindex');
  });
});
