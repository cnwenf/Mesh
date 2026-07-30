/**
 * TopBar — 品牌/搜索/连接状态点(文本始终在场)/命令面板与帮助入口回调。
 */
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { TopBar } from '../TopBar';
import type { ConnectionState } from '../../realtime';

const LABELS: Record<ConnectionState, string> = {
  idle: 'Not connected',
  connecting: 'Connecting',
  connected: 'Connected',
  reconnecting: 'Reconnecting',
  offline: 'Offline',
  resyncing: 'Resyncing',
};

describe('TopBar', () => {
  it('渲染品牌与全局搜索框', () => {
    renderWithProviders(<TopBar state="idle" onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} onOpenSearch={vi.fn()} />);
    expect(screen.getByText('Mesh')).toBeInTheDocument();
    expect(screen.getByTestId('topbar-search')).toBeInTheDocument();
  });

  it.each(Object.entries(LABELS))('连接状态 %s 的文本标签始终呈现', (state, label) => {
    renderWithProviders(
      <TopBar state={state as ConnectionState} onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} onOpenSearch={vi.fn()} />,
    );
    expect(screen.getByTestId('conn-status').textContent).toContain(label);
  });

  it('命令面板与帮助按钮触发对应回调', () => {
    const onOpenPalette = vi.fn();
    const onOpenHelp = vi.fn();
    renderWithProviders(<TopBar state="connected" onOpenPalette={onOpenPalette} onOpenHelp={onOpenHelp} onOpenSearch={vi.fn()} />);
    fireEvent.click(screen.getByTestId('open-palette'));
    fireEvent.click(screen.getByTestId('open-help'));
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
    expect(onOpenHelp).toHaveBeenCalledTimes(1);
  });

  it('顶栏搜索是统一搜索入口:键入即携带查询展开面板并交接焦点(design-quality A-02 / S1)', () => {
    const onOpenSearch = vi.fn();
    renderWithProviders(<TopBar state="connected" onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} onOpenSearch={onOpenSearch} />);
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: '看' } });
    expect(onOpenSearch).toHaveBeenCalledWith('看');
    // 交接后本框清空(焦点将由命令面板接管)
    expect(input).toHaveValue('');
  });

  it('顶栏搜索 Enter 携带当前查询展开面板', () => {
    const onOpenSearch = vi.fn();
    renderWithProviders(<TopBar state="connected" onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} onOpenSearch={onOpenSearch} />);
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onOpenSearch).toHaveBeenCalledWith('');
  });

  it('顶栏搜索清空输入不展开面板(仅复位本框)', () => {
    const onOpenSearch = vi.fn();
    renderWithProviders(<TopBar state="connected" onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} onOpenSearch={onOpenSearch} />);
    const input = screen.getByTestId('topbar-search');
    fireEvent.change(input, { target: { value: '' } });
    expect(onOpenSearch).not.toHaveBeenCalled();
    expect(input).toHaveValue('');
  });

  it('顶栏搜索 Escape 清空输入(不展开面板)', () => {
    const onOpenSearch = vi.fn();
    renderWithProviders(<TopBar state="connected" onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} onOpenSearch={onOpenSearch} />);
    const input = screen.getByTestId('topbar-search');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onOpenSearch).not.toHaveBeenCalled();
    expect(input).toHaveValue('');
  });
});
