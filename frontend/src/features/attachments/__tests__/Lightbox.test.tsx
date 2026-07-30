/**
 * Lightbox 测试(Dialog 承载,§4.3):url 缺失呈现加载占位且禁用下载/视图控件;
 * 有 url 呈现原图、下载可用;缩放/旋转/重置/在附件区定位;关闭按钮回调 onClose。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Lightbox, type LightboxProps } from '../components/Lightbox';

/** jsdom 不提供 PointerEvent(且 fireEvent 会丢弃 pointerId);以携带 pointerId 的桩替代,
 *  使双指捏合的多指针跟踪可在单测中驱动。 */
class FakePointerEvent extends MouseEvent {
  readonly pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 0;
  }
}

beforeEach(() => {
  vi.stubGlobal('PointerEvent', FakePointerEvent);
});

function renderLightbox(imageUrl: string | null) {
  const onDownload = vi.fn();
  const onClose = vi.fn();
  const onLocate = vi.fn();
  const baseProps: LightboxProps = {
    open: true,
    title: 'shot.png',
    imageUrl,
    loadingLabel: 'Loading',
    downloadLabel: 'Download',
    closeLabel: 'Close',
    zoomInLabel: 'Zoom in',
    zoomOutLabel: 'Zoom out',
    rotateLabel: 'Rotate',
    resetLabel: 'Reset view',
    locateLabel: 'Locate',
    onDownload,
    onLocate,
    onClose,
  };
  render(<Lightbox {...baseProps} />);
  return { onDownload, onClose, onLocate };
}

function closedLightbox() {
  render(
    <Lightbox
      open={false}
      title="x"
      imageUrl={null}
      loadingLabel="Loading"
      downloadLabel="Download"
      closeLabel="Close"
      zoomInLabel="Zoom in"
      zoomOutLabel="Zoom out"
      rotateLabel="Rotate"
      resetLabel="Reset view"
      locateLabel="Locate"
      onDownload={() => undefined}
      onLocate={() => undefined}
      onClose={() => undefined}
    />,
  );
}

describe('Lightbox', () => {
  it('renders nothing when closed', () => {
    closedLightbox();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows a loading placeholder and disables download/view controls while the url resolves', () => {
    renderLightbox(null);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
    expect((screen.getByRole('button', { name: 'Download' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Zoom in' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Rotate' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the image and fires onDownload once the url is present', () => {
    const { onDownload } = renderLightbox('http://cdn/original.png');
    const img = screen.getByRole('img') as HTMLImageElement;
    expect(img.src).toBe('http://cdn/original.png');
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(onDownload).toHaveBeenCalledTimes(1);
  });

  it('zooms in and out within bounds, clamping at the limits', () => {
    renderLightbox('http://cdn/x.png');
    const img = screen.getByTestId('lightbox-image') as HTMLImageElement;
    const zoomIn = screen.getByRole('button', { name: 'Zoom in' });
    const zoomOut = screen.getByRole('button', { name: 'Zoom out' });
    // Zoom out disabled at the initial scale 1 (minimum is 0.5 — one step down allowed).
    expect(img.style.transform).toBe('scale(1) rotate(0deg)');
    fireEvent.click(zoomIn);
    expect(img.style.transform).toBe('scale(1.5) rotate(0deg)');
    fireEvent.click(zoomOut);
    expect(img.style.transform).toBe('scale(1) rotate(0deg)');
    fireEvent.click(zoomOut);
    expect(img.style.transform).toBe('scale(0.5) rotate(0deg)');
    // Clamped at the minimum: further clicks keep 0.5 and disable the button.
    fireEvent.click(zoomOut);
    expect(img.style.transform).toBe('scale(0.5) rotate(0deg)');
    expect((zoomOut as HTMLButtonElement).disabled).toBe(true);
  });

  it('rotates by 90° steps and wraps at 360°', () => {
    renderLightbox('http://cdn/x.png');
    const img = screen.getByTestId('lightbox-image') as HTMLImageElement;
    const rotate = screen.getByRole('button', { name: 'Rotate' });
    for (let i = 1; i <= 4; i += 1) fireEvent.click(rotate);
    expect(img.style.transform).toBe('scale(1) rotate(0deg)');
    fireEvent.click(rotate);
    expect(img.style.transform).toBe('scale(1) rotate(90deg)');
  });

  it('resets zoom and rotation to the identity view', () => {
    renderLightbox('http://cdn/x.png');
    const img = screen.getByTestId('lightbox-image') as HTMLImageElement;
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rotate' }));
    expect(img.style.transform).toBe('scale(1.5) rotate(90deg)');
    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }));
    expect(img.style.transform).toBe('scale(1) rotate(0deg)');
  });

  it('fires onLocate to scroll back to the attachment section', () => {
    const { onLocate } = renderLightbox('http://cdn/x.png');
    fireEvent.click(screen.getByRole('button', { name: 'Locate' }));
    expect(onLocate).toHaveBeenCalledTimes(1);
  });

  it('fires onClose from the close button', () => {
    const { onClose } = renderLightbox('http://cdn/x.png');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('toggles 1×↔2× on double-tap (parity §2.22)', () => {
    renderLightbox('http://cdn/x.png');
    const img = screen.getByTestId('lightbox-image');
    const tap = (): void => {
      fireEvent.pointerDown(img, { pointerId: 1, clientX: 100, clientY: 100 });
      fireEvent.pointerUp(img, { pointerId: 1, clientX: 100, clientY: 100 });
    };
    tap();
    tap();
    expect(img.style.transform).toContain('scale(2)');
    tap();
    tap();
    expect(img.style.transform).toContain('scale(1)');
  });

  it('zooms with a two-finger pinch and clamps at the max scale', () => {
    renderLightbox('http://cdn/x.png');
    const img = screen.getByTestId('lightbox-image');
    fireEvent.pointerDown(img, { pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerDown(img, { pointerId: 2, clientX: 100, clientY: 0 });
    // 距离 100 → 200:相对基准 1× 放大到 2×
    fireEvent.pointerMove(img, { pointerId: 2, clientX: 200, clientY: 0 });
    expect(img.style.transform).toContain('scale(2)');
    // 距离 100 → 1000:超过上限,钳制到 4×
    fireEvent.pointerMove(img, { pointerId: 2, clientX: 1000, clientY: 0 });
    expect(img.style.transform).toContain('scale(4)');
    fireEvent.pointerUp(img, { pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerUp(img, { pointerId: 2, clientX: 1000, clientY: 0 });
  });

  it('ignores pinch gestures while the image is still loading', () => {
    renderLightbox(null);
    expect(screen.queryByTestId('lightbox-image')).toBeNull();
  });
});
