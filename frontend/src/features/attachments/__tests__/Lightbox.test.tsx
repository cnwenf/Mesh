/**
 * Lightbox 测试(Dialog 承载,§4.3):url 缺失呈现加载占位且禁用下载;有 url 呈现原图、
 * 下载可用;关闭按钮回调 onClose。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Lightbox } from '../components/Lightbox';

function renderLightbox(imageUrl: string | null) {
  const onDownload = vi.fn();
  const onClose = vi.fn();
  render(
    <Lightbox
      open
      title="shot.png"
      imageUrl={imageUrl}
      loadingLabel="Loading"
      downloadLabel="Download"
      closeLabel="Close"
      onDownload={onDownload}
      onClose={onClose}
    />,
  );
  return { onDownload, onClose };
}

describe('Lightbox', () => {
  it('renders nothing when closed', () => {
    render(
      <Lightbox
        open={false}
        title="x"
        imageUrl={null}
        loadingLabel="Loading"
        downloadLabel="Download"
        closeLabel="Close"
        onDownload={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows a loading placeholder and disables download while the url resolves', () => {
    renderLightbox(null);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
    expect((screen.getByRole('button', { name: 'Download' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the image and fires onDownload once the url is present', () => {
    const { onDownload } = renderLightbox('http://cdn/original.png');
    const img = screen.getByRole('img') as HTMLImageElement;
    expect(img.src).toBe('http://cdn/original.png');
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(onDownload).toHaveBeenCalledTimes(1);
  });

  it('fires onClose from the close button', () => {
    const { onClose } = renderLightbox('http://cdn/x.png');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
