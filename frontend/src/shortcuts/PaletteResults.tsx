/**
 * 面板结果列表(命令面板对话框与顶栏搜索弹层共用,§4.9 同一组件同一数据源)。
 *
 * - 分组组头(i18n 键)+ 扁平 role=option 列表(DOM id `palette-opt-{stableId}`,
 *   aria-activedescendant 锚点;§4.3.1 稳定选择);
 * - 命中标题经 highlightRangesToSpans 以 <mark>(字重 + 下划线,颜色非唯一信号,§6.12)渲染;
 * - 徽章经消息目录键 + 参数组装(§6.18),tone 映射自 badge.color 语义名;
 * - 检索中呈现 skeleton(不阻塞本地命令,§11.4;prefers-reduced-motion 由 CSS 降级);
 * - 纯呈现:键盘语义在调用方(对话框/顶栏)处理,本组件经回调上报 hover/激活。
 */
import { Badge, Icon, Kbd, Skeleton } from '../design';
import type { BadgeTone } from '../design';
import { useT } from '../i18n';
import { formatCombo } from './ShortcutProvider';
import { optionDomId } from './paletteModel';
import type { PaletteOption, PaletteSection, PaletteSubtitle } from './paletteModel';
import { highlightRangesToSpans } from '../api/search';
import type { SearchBadge } from '../api/search';
import './shortcuts.css';

export interface PaletteResultsProps {
  readonly sections: ReadonlyArray<PaletteSection>;
  readonly selectedStableId: string | null;
  readonly onOptionHover: (stableId: string) => void;
  readonly onOptionActivate: (option: PaletteOption, opts: { newTab: boolean }) => void;
  /** 实体检索中:组尾 skeleton(命令组不受影响) */
  readonly isSearching: boolean;
  /** skeleton 的 sr-only 加载文案 */
  readonly skeletonLabel: string;
  readonly listId: string;
  readonly listLabel: string;
}

/** badge.color 语义名 → Badge tone(status/danger/warn/success/info,§3.2) */
export function badgeToneForColor(color: string): BadgeTone {
  switch (color) {
    case 'success':
      return 'success';
    case 'danger':
      return 'danger';
    case 'warn':
    case 'warning':
      return 'warning';
    case 'info':
      return 'info';
    case 'accent':
      return 'accent';
    default:
      return 'neutral';
  }
}

function BadgeChip(props: { badge: SearchBadge }): React.JSX.Element {
  const { badge } = props;
  const t = useT();
  return (
    <Badge tone={badgeToneForColor(badge.color)} size="sm" icon={null}>
      {t(badge.label_key, badge.label_params)}
    </Badge>
  );
}

/** 副标题本地化:少数参数为稳定枚举键(memberType/scope/visibility),经目录二次解析 */
function SubtitleText(props: { subtitle: PaletteSubtitle }): React.JSX.Element {
  const { subtitle } = props;
  const t = useT();
  const params: Record<string, string | number> = { ...subtitle.params };
  if (subtitle.key === 'search.subtitle.member' && typeof params.memberType === 'string') {
    params.memberType = t(`search.memberType.${params.memberType}`);
  }
  if (subtitle.key === 'search.subtitle.view' && typeof params.scope === 'string') {
    params.scope = t(`search.viewScope.${params.scope}`);
  }
  if (subtitle.key === 'search.subtitle.project' && typeof params.visibility === 'string') {
    params.visibility = t(`search.visibility.${params.visibility}`);
  }
  return <span className="mesh-palette__subtitle">{t(subtitle.key, params)}</span>;
}

/** 命中标题:code point 区间 → <mark> 分段(字重 + 下划线,§6.12;title 属性给全文) */
export function HighlightedTitle(props: {
  title: string;
  option: PaletteOption;
}): React.JSX.Element {
  const { title, option } = props;
  const ranges = option.highlight?.title.ranges;
  if (ranges === undefined || ranges.length === 0) {
    return (
      <span className="mesh-palette__title" title={title}>
        {title}
      </span>
    );
  }
  const spans = highlightRangesToSpans(title, ranges);
  return (
    <span className="mesh-palette__title" title={title}>
      {spans.map((span, index) =>
        span.marked ? (
          <mark key={index} className="mesh-palette__mark mesh-palette__hit">
            {span.text}
          </mark>
        ) : (
          <span key={index}>{span.text}</span>
        ),
      )}
    </span>
  );
}

interface OptionRowProps {
  readonly option: PaletteOption;
  readonly selected: boolean;
  readonly onHover: (stableId: string) => void;
  readonly onActivate: (option: PaletteOption, opts: { newTab: boolean }) => void;
}

function OptionRow(props: OptionRowProps): React.JSX.Element {
  const { option, selected, onHover, onActivate } = props;
  const domId = optionDomId(option.stableId);
  return (
    <li
      id={domId}
      data-testid={domId}
      role="option"
      aria-selected={selected}
      className={
        selected ? 'mesh-palette__option mesh-palette__option--active' : 'mesh-palette__option'
      }
      onMouseEnter={() => onHover(option.stableId)}
      onClick={() => onActivate(option, { newTab: false })}
      onAuxClick={(event) => {
        if (event.button === 1) {
          event.preventDefault();
          onActivate(option, { newTab: true });
        }
      }}
    >
      <span className="mesh-palette__option-icon" aria-hidden="true">
        <Icon name={option.icon} size={16} />
      </span>
      <span className="mesh-palette__option-main">
        <HighlightedTitle title={option.title} option={option} />
        {option.subtitle !== undefined ? <SubtitleText subtitle={option.subtitle} /> : null}
      </span>
      {option.badge !== undefined ? <BadgeChip badge={option.badge} /> : null}
      {option.combo !== undefined ? (
        <span className="mesh-palette__combo">
          <Kbd>{formatCombo(option.combo)}</Kbd>
        </span>
      ) : null}
    </li>
  );
}

export function PaletteResults(props: PaletteResultsProps): React.JSX.Element {
  const {
    sections,
    selectedStableId,
    onOptionHover,
    onOptionActivate,
    isSearching,
    skeletonLabel,
    listId,
    listLabel,
  } = props;
  const t = useT();
  return (
    <ul id={listId} role="listbox" className="mesh-palette__list" aria-label={listLabel}>
      {sections.map((section) => (
        <li key={section.key} role="group" aria-label={t(section.labelKey)}>
          <div className="mesh-palette__group-title">{t(section.labelKey)}</div>
          <ul className="mesh-palette__group-list">
            {section.options.map((option) => (
              <OptionRow
                key={option.stableId}
                option={option}
                selected={option.stableId === selectedStableId}
                onHover={onOptionHover}
                onActivate={onOptionActivate}
              />
            ))}
          </ul>
        </li>
      ))}
      {isSearching ? (
        <li className="mesh-palette__skeletons" aria-hidden="false">
          <Skeleton loadingLabel={skeletonLabel} className="mesh-palette__skeleton-row" />
        </li>
      ) : null}
    </ul>
  );
}
