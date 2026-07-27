/**
 * 图片灯箱(attachment.md §4.3:点击缩略图查看原图 + 下载)。
 * 经 Dialog 承载(焦点圈养 / Esc 关闭 / aria-modal)。原图签名 URL 由父级解析后传入;
 * url 为 null 时呈现加载中占位。所有文案来自 prop,无硬编码可见字符串。
 */
import { Button, Dialog, Skeleton } from '../../../design';
import '../attachments.css';

export interface LightboxProps {
  readonly open: boolean;
  /** 对话框标题(通常为文件名) */
  readonly title: string;
  /** 原图签名 URL;null 表示尚未解析(加载中) */
  readonly imageUrl: string | null;
  readonly loadingLabel: string;
  readonly downloadLabel: string;
  readonly closeLabel: string;
  readonly onDownload: () => void;
  readonly onClose: () => void;
}

export function Lightbox(props: LightboxProps): React.JSX.Element {
  return (
    <Dialog open={props.open} onClose={props.onClose} title={props.title} closeLabel={props.closeLabel}>
      <div className="mesh-attachments-lightbox">
        {props.imageUrl === null ? (
          <Skeleton loadingLabel={props.loadingLabel} className="mesh-attachments-lightbox__loading" />
        ) : (
          <img className="mesh-attachments-lightbox__image" src={props.imageUrl} alt={props.title} />
        )}
        <div className="mesh-attachments-lightbox__actions">
          <Button variant="secondary" size="sm" onClick={props.onDownload} disabled={props.imageUrl === null}>
            {props.downloadLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
