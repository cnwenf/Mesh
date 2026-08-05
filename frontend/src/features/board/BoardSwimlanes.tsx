/**
 * 二维看板泳道。列骨架由顶层 columns 共享，每个 lane 只提供本地
 * cell 的 count/data。所有 cell 参与同一拖拽命中域，因此跨列、跨泳道
 * 都会回传完整的 (group_key, sub_group_key)。
 */
import { useMemo, useState } from 'react';
import { Button } from '../../design';
import { BoardColumns } from './BoardColumns';
import type { BoardDropTarget } from './BoardColumns';
import { useIsCompactViewport } from './BoardCompact';
import { columnLabelKey } from './columns';
import type { BoardLane, BoardProjectionColumn } from './projection';
import type { BoardColumn } from './types';
import type { DragState } from './useBoardDrag';
import './board.css';

interface BoardSwimlanesProps {
  readonly columns: readonly BoardProjectionColumn[];
  readonly lanes: readonly BoardLane[];
  readonly groupBy: string;
  readonly subGroupBy: string;
  readonly collapsedColumns: readonly string[];
  readonly cardFields?: readonly string[];
  readonly canWrite: boolean;
  readonly dragEnabled: boolean;
  readonly onToggleCollapse: (key: string) => void;
  readonly onDropCard: (
    issueId: string,
    toGroupKey: string,
    position: number,
    toSubGroupKey: string,
  ) => void;
  readonly onQuickCreate: (
    groupKey: string,
    title: string,
    subGroupKey: string,
  ) => void | Promise<void>;
  readonly highlightCardId?: string | null;
  readonly executionStatusByIssueId?: Readonly<Record<string, string>>;
}

function cellKey(laneKey: string, groupKey: string): string {
  return JSON.stringify([laneKey, groupKey]);
}

function columnLabel(column: BoardProjectionColumn, groupBy: string): string {
  return groupBy === 'state_category' || groupBy === 'priority'
    ? columnLabelKey(groupBy, column.key)
    : column.label;
}

export function BoardSwimlanes(props: BoardSwimlanesProps): React.JSX.Element {
  const {
    columns,
    lanes,
    groupBy,
    subGroupBy,
    collapsedColumns,
    cardFields,
    canWrite,
    dragEnabled,
    onToggleCollapse,
    onDropCard,
    onQuickCreate,
    highlightCardId,
    executionStatusByIssueId = {},
  } = props;
  const isCompact = useIsCompactViewport();
  const [activeLaneKey, setActiveLaneKey] = useState<string | null>(lanes[0]?.key ?? null);
  const [dragState, setDragState] = useState<DragState | null>(null);
  const collapsed = useMemo(() => new Set(collapsedColumns), [collapsedColumns]);
  const globalColumns = useMemo<readonly BoardColumn[]>(
    () =>
      columns.map((column) => ({
        ...column,
        label: columnLabel(column, groupBy),
        collapsed: collapsed.has(column.key),
        placeholder: false,
      })),
    [collapsed, columns, groupBy],
  );
  const dropTargets = useMemo<readonly BoardDropTarget[]>(
    () =>
      lanes.flatMap((lane) =>
        globalColumns.map((column) => {
          const group = lane.groups.find((candidate) => candidate.key === column.key);
          return {
            key: cellKey(lane.key, column.key),
            groupKey: column.key,
            subGroupKey: lane.key,
            label: `${lane.label} / ${column.label}`,
            // WIP 是主列跨 lane 聚合的限制，命中预检必须使用顶层 count。
            column,
            cards: group?.data ?? [],
          };
        }),
      ),
    [globalColumns, lanes],
  );
  const visibleLanes =
    isCompact && lanes.length > 0
      ? [lanes.find((lane) => lane.key === activeLaneKey) ?? lanes[0]].filter(
          (lane): lane is BoardLane => lane !== undefined,
        )
      : lanes;

  return (
    <section
      className="mesh-board__swimlanes"
      data-testid="board-swimlanes"
      data-board-drag-scope
      aria-label={subGroupBy}
    >
      {isCompact && lanes.length > 1 ? (
        <nav className="mesh-board__lane-tabs" aria-label={subGroupBy}>
          {lanes.map((lane) => (
            <Button
              key={lane.key}
              variant={lane.key === activeLaneKey ? 'primary' : 'secondary'}
              size="sm"
              aria-pressed={lane.key === activeLaneKey}
              onClick={() => setActiveLaneKey(lane.key)}
            >
              {lane.label} ({lane.count})
            </Button>
          ))}
        </nav>
      ) : null}
      <div className="mesh-board__swimlane-grid" data-testid="board-swimlane-grid">
        {visibleLanes.map((lane) => {
          const cardsByKey = Object.fromEntries(
            lane.groups.map((group) => [group.key, group.data]),
          );
          const laneColumns = globalColumns.map((column) => ({
            ...column,
            count: lane.groups.find((group) => group.key === column.key)?.count ?? 0,
          }));
          return (
            <section
              key={lane.key}
              className="mesh-board__swimlane"
              data-testid={`board-swimlane-${lane.key}`}
            >
              <header className="mesh-board__swimlane-head">
                <h2>{lane.label}</h2>
                <span>{lane.count}</span>
              </header>
              <BoardColumns
                columns={laneColumns}
                groupBy={groupBy}
                cardsByKey={cardsByKey}
                cardFields={cardFields}
                canWrite={canWrite}
                dragEnabled={dragEnabled}
                subGroupKey={lane.key}
                dropTargets={dropTargets}
                sharedDragState={dragState}
                onDragStateChange={setDragState}
                onToggleCollapse={onToggleCollapse}
                onDropCard={(issueId, toGroupKey, position, toSubGroupKey) =>
                  onDropCard(issueId, toGroupKey, position, toSubGroupKey ?? lane.key)
                }
                onQuickCreate={(groupKey, title, targetLaneKey) =>
                  onQuickCreate(groupKey, title, targetLaneKey ?? lane.key)
                }
                highlightCardId={highlightCardId}
                executionStatusByIssueId={executionStatusByIssueId}
              />
            </section>
          );
        })}
      </div>
    </section>
  );
}
