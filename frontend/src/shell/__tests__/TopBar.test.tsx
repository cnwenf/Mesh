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
    renderWithProviders(<TopBar state="idle" onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} />);
    expect(screen.getByText('Mesh')).toBeInTheDocument();
    expect(screen.getByTestId('topbar-search')).toBeInTheDocument();
  });

  it.each(Object.entries(LABELS))('连接状态 %s 的文本标签始终呈现', (state, label) => {
    renderWithProviders(
      <TopBar state={state as ConnectionState} onOpenPalette={vi.fn()} onOpenHelp={vi.fn()} />,
    );
    expect(screen.getByTestId('conn-status').textContent).toContain(label);
  });

  it('命令面板与帮助按钮触发对应回调', () => {
    const onOpenPalette = vi.fn();
    const onOpenHelp = vi.fn();
    renderWithProviders(<TopBar state="connected" onOpenPalette={onOpenPalette} onOpenHelp={onOpenHelp} />);
    fireEvent.click(screen.getByTestId('open-palette'));
    fireEvent.click(screen.getByTestId('open-help'));
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
    expect(onOpenHelp).toHaveBeenCalledTimes(1);
  });
});
