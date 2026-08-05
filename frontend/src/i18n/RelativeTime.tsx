/**
 * 共享相对时间呈现——i18n.md §4.3/§4.4。
 * 可见文案按 locale 渲染并自动推进；tooltip 同时保留用户时区下的
 * 本地时间、时区标注与传输层 UTC 原值。
 */
import { useEffect, useState } from 'react';
import { Tooltip } from '../design/components/Tooltip';
import { useSettingsStore } from '../state/settingsStore';
import { formatRelativeTime, formatWithZoneAnnotation } from './format';
import { useT } from './I18nProvider';
import './RelativeTime.css';

const REFRESH_INTERVAL_MS = 30_000;

export interface RelativeTimeProps {
  readonly utcIso: string;
  readonly locale: string;
  /** 显式时区覆盖；缺省时响应式读取 users.timezone。 */
  readonly timeZone?: string;
  readonly className?: string;
}

export function RelativeTime(props: RelativeTimeProps): React.JSX.Element {
  const preferredTimeZone = useSettingsStore((state) => state.preferences.timezone);
  const timeZone = props.timeZone ?? preferredTimeZone;
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [revealed, setRevealed] = useState(false);
  const t = useT();

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now());
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, []);

  const local = formatWithZoneAnnotation(props.utcIso, {
    locale: props.locale,
    timeZone,
  });
  const tooltip = t('time.sharedTooltip', { local, utc: props.utcIso });
  const relative = formatRelativeTime(props.utcIso, {
    locale: props.locale,
    now: new Date(nowMs),
  });

  return (
    <Tooltip
      content={tooltip}
      className={`mesh-relative-time${revealed ? ' mesh-relative-time--revealed' : ''}`}
    >
      <button
        type="button"
        className="mesh-relative-time__trigger"
        aria-expanded={revealed}
        onClick={() => setRevealed((current) => !current)}
        onBlur={() => setRevealed(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setRevealed(false);
        }}
      >
        <time className={props.className} dateTime={props.utcIso}>
          {relative}
        </time>
      </button>
    </Tooltip>
  );
}
