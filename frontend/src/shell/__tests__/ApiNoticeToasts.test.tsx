/**
 * ApiNoticeToasts 桥测试(L252)— client 拦截层契约通知 → ToastProvider 用户可见提示。
 * 覆盖:429 退避(带/不带 Retry-After 秒数)、Deprecation/Sunset 升级提示、卸载后不再接收。
 */
import { act } from '@testing-library/react';
import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { notifyDeprecation, notifyRateLimited, resetApiNoticeState } from '../../api/notices';
import { renderWithProviders } from '../../test-utils/render';
import { ApiNoticeToasts } from '../ApiNoticeToasts';

beforeEach(() => {
  resetApiNoticeState();
});

afterEach(() => {
  resetApiNoticeState();
});

describe('ApiNoticeToasts:API 契约通知的用户可见呈现(L252)', () => {
  it('429 退避提示展示 Retry-After 秒数', () => {
    renderWithProviders(<ApiNoticeToasts />);
    act(() => {
      notifyRateLimited(30);
    });
    expect(screen.getByText('Too many requests — retry in 30 seconds.')).toBeInTheDocument();
  });

  it('Retry-After 为 1 秒时使用单数形式', () => {
    renderWithProviders(<ApiNoticeToasts />);
    act(() => {
      notifyRateLimited(1);
    });
    expect(screen.getByText('Too many requests — retry in 1 second.')).toBeInTheDocument();
  });

  it('429 无 Retry-After → 仍提示稍后重试(失败必须可见)', () => {
    renderWithProviders(<ApiNoticeToasts />);
    act(() => {
      notifyRateLimited(undefined);
    });
    expect(screen.getByText('Too many requests — please try again shortly.')).toBeInTheDocument();
  });

  it('Deprecation/Sunset → 一次性升级提示', () => {
    renderWithProviders(<ApiNoticeToasts />);
    act(() => {
      notifyDeprecation('true', null);
    });
    expect(
      screen.getByText('This API version is being retired — please upgrade Mesh.'),
    ).toBeInTheDocument();
  });

  it('卸载后不再接收通知(订阅随组件清理)', () => {
    const { unmount } = renderWithProviders(<ApiNoticeToasts />);
    unmount();
    act(() => {
      notifyRateLimited(30);
    });
    expect(screen.queryByText(/Too many requests/)).not.toBeInTheDocument();
  });
});
