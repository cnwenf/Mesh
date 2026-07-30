/**
 * 组件状态 fixture 页(design-quality §12 Phase 1 退出条件 / MES-115 视觉回归基础):
 * 以静态、确定性数据平铺全部基础组件与其状态矩阵,作为 1440/1024/768/390 ×
 * light/dark 视觉回归的拍摄对象与组件走查清单。
 *
 * - 公开路由 /styleguide(设计系统自证页,不含业务数据、不需鉴权);
 * - 无时间戳/随机量,双主题截图可复现;主题经应用既有 ThemeProvider 协商,
 *   暗色拍摄由视觉套件切换 data-theme 完成;
 * - 仅组合 design 自身组件(依赖方向合规:patterns ← primitives ← foundations)。
 */
import { useState } from 'react';
import { Avatar } from './components/Avatar';
import { Badge } from './components/Badge';
import { Banner } from './components/Banner';
import { Button } from './components/Button';
import { Checkbox } from './components/Checkbox';
import { DataTable } from './components/DataTable';
import type { DataTableColumn, DataTableSortState } from './components/DataTable';
import { EmptyState } from './components/EmptyState';
import { ErrorState } from './components/ErrorState';
import { Icon, ICON_NAMES } from './components/icons';
import { IconButton } from './components/IconButton';
import { Input } from './components/Input';
import { Kbd } from './components/Kbd';
import { Menu } from './components/Menu';
import { PageHeader } from './components/PageHeader';
import { Popover } from './components/Popover';
import { Select } from './components/Select';
import { Skeleton } from './components/Skeleton';
import { StatusDot } from './components/StatusDot';
import { Switch } from './components/Switch';
import { Tabs, TabsList, TabsPanel, TabsTrigger } from './components/Tabs';
import { Textarea } from './components/Textarea';
import { Toolbar } from './components/Toolbar';
import { Tooltip } from './components/Tooltip';
import './components/styleguide.css';

interface FixtureRow {
  readonly id: string;
  readonly key: string;
  readonly title: string;
  readonly owner: string;
  readonly points: number;
}

const TABLE_ROWS: ReadonlyArray<FixtureRow> = [
  { id: '1', key: 'MESH-1', title: '设计系统底座收敛', owner: '林一', points: 8 },
  { id: '2', key: 'MESH-2', title: '全局壳层分组侧栏', owner: '陈二', points: 5 },
  { id: '3', key: 'MESH-3', title: '视觉回归基础', owner: '周三', points: 3 },
];

const TABLE_COLUMNS: ReadonlyArray<DataTableColumn<FixtureRow>> = [
  { id: 'key', header: '编号', cell: (row) => <code className="styleguide__mono">{row.key}</code>, sortable: true },
  { id: 'title', header: '标题', cell: (row) => row.title, sortable: true },
  { id: 'owner', header: '负责人', cell: (row) => row.owner },
  { id: 'points', header: '点数', cell: (row) => row.points, align: 'end', sortable: true },
];

/** fixture 用无副作用回调(禁用控件/纯演示项的占位回调,集中一处便于覆盖) */
const noop = (): void => undefined;

function Section(props: { title: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <section className="styleguide__section" data-testid={'styleguide-' + props.title}>
      <h2 className="styleguide__heading">{props.title}</h2>
      {props.children}
    </section>
  );
}

export function StyleguidePage(): React.JSX.Element {
  const [switchOn, setSwitchOn] = useState(true);
  const [sort, setSort] = useState<DataTableSortState>({ id: 'key', direction: 'asc' });

  return (
    <div className="styleguide">
      <PageHeader
        title="Mesh 设计系统"
        description="基础组件状态矩阵与排版/令牌走查 fixture;视觉回归拍摄对象(亮暗双主题、四视口)。"
      />

      <Section title="按钮">
        <div className="styleguide__row">
          <Button variant="primary">主要按钮</Button>
          <Button variant="secondary">次要按钮</Button>
          <Button variant="ghost">幽灵按钮</Button>
          <Button variant="danger">危险按钮</Button>
          <Button variant="primary" disabled>
            禁用
          </Button>
          <Button variant="primary" isLoading>
            提交中
          </Button>
        </div>
        <div className="styleguide__row">
          <Button variant="secondary" size="sm">
            小 28
          </Button>
          <Button variant="secondary" size="md">
            中 36
          </Button>
          <Button variant="secondary" size="lg">
            大 44
          </Button>
          <Tooltip label="删除此项">
            <IconButton label="删除">
              <Icon name="trash" size={20} />
            </IconButton>
          </Tooltip>
          <Menu
            trigger={<Icon name="more-horizontal" size={20} />}
            triggerLabel="行操作"
            label="示例行操作"
            items={[
              { id: 'edit', label: '编辑', icon: <Icon name="edit" size={16} />, onSelect: () => undefined },
              { id: 'del', label: '删除', icon: <Icon name="trash" size={16} />, danger: true, onSelect: noop },
            ]}
          />
          <Popover trigger={<Icon name="filter" size={20} />} triggerLabel="筛选" label="筛选面板">
            <p className="styleguide__note">筛选器内容示例</p>
          </Popover>
        </div>
      </Section>

      <Section title="图标">
        <ul className="styleguide__icon-grid">
          {ICON_NAMES.map((name) => (
            <li key={name} className="styleguide__icon-cell">
              <Icon name={name} size={20} />
              <span className="styleguide__icon-name">{name}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="表单">
        <div className="styleguide__form-grid">
          <Input label="标题" placeholder="输入工作项标题" hint="失焦后校验格式" />
          <Input label="邮箱" type="email" size="lg" defaultValue="mesh@example.com" error="邮箱格式不正确" />
          <Select label="状态" defaultValue="todo">
            <option value="todo">待办</option>
            <option value="doing">进行中</option>
            <option value="done">已完成</option>
          </Select>
          <Textarea label="描述" hint="支持 Markdown" defaultValue={'第一行\n第二行'} />
          <Checkbox label="订阅通知" description="有人提及我时通知我" defaultChecked />
          <Checkbox label="半选父项" indeterminate onChange={noop} />
          <Switch label="仅显示我的" description="列表默认按负责人过滤" checked={switchOn} onCheckedChange={setSwitchOn} />
          <Switch label="锁定项" checked={false} onCheckedChange={noop} disabled />
        </div>
      </Section>

      <Section title="徽标与头像">
        <div className="styleguide__row">
          <Badge tone="success" label="成功" />
          <Badge tone="warn" label="注意" />
          <Badge tone="danger" label="失败" />
          <Badge tone="info" label="同步中" />
          <Badge tone="neutral" label="草稿" />
          <Badge tone="accent" icon={<Icon name="sparkles" size={16} />} label="AI 运行中" />
        </div>
        <div className="styleguide__row">
          <Avatar name="林一" size={20} />
          <Avatar name="陈二" size={24} />
          <Avatar name="周三" size={32} />
          <Avatar name=" Mesh Agent" kind="agent" size={40} />
          <Avatar name="赵四" size={56} />
          <StatusDot tone="success" label="在线" />
          <StatusDot tone="info" label="运行中" pulse />
          <StatusDot tone="danger" label="失败" />
          <span>
            快捷键 <Kbd>⌘</Kbd> <Kbd>K</Kbd>
          </span>
        </div>
      </Section>

      <Section title="标签页">
        <Tabs defaultValue="overview">
          <TabsList label="详情分区">
            <TabsTrigger value="overview">概览</TabsTrigger>
            <TabsTrigger value="activity">动态</TabsTrigger>
            <TabsTrigger value="settings">设置</TabsTrigger>
          </TabsList>
          <TabsPanel value="overview">概览内容:对象摘要与关键指标。</TabsPanel>
          <TabsPanel value="activity">动态内容:活动时间线。</TabsPanel>
          <TabsPanel value="settings">设置内容:二级配置。</TabsPanel>
        </Tabs>
      </Section>

      <Section title="反馈与状态">
        <div className="styleguide__stack">
          <Banner tone="info">信息提示:同步正在进行。</Banner>
          <Banner tone="success">操作成功:已保存。</Banner>
          <Banner tone="warn">注意:接近用量上限。</Banner>
          <Banner tone="danger">失败:提交未保存。</Banner>
          <Skeleton loadingLabel="示例加载中" />
          <EmptyState
            illustration={<Icon name="inbox" size={24} />}
            title="还没有收件"
            description="提及、分派与状态变化会汇聚到这里。"
            action={<Button variant="primary">创建工作项</Button>}
          />
          <ErrorState
            title="加载失败"
            description="列表未能载入,已加载的筛选条件仍然保留。"
            impact="不影响其他页面的已有数据。"
            onRetry={noop}
            retryLabel="重试"
            diagnosticId="diag-0001"
            copyLabel="复制诊断编号"
          />
        </div>
      </Section>

      <Section title="页头 / 工具条 / 表格">
        {/* DataView 模板演示:页头级标题在本 fixture 内降为 h3(全页唯一 h1 在页首,§1.2) */}
        <div className="styleguide__stack">
          <h3 className="mesh-text--title-2">工作项</h3>
        </div>
        <Toolbar label="视图与筛选">
          <Button variant="ghost" size="sm">
            筛选
          </Button>
          <Button variant="ghost" size="sm">
            排序
          </Button>
        </Toolbar>
        <DataTable
          caption="示例工作项(视觉 fixture)"
          hideCaption
          columns={TABLE_COLUMNS}
          rows={TABLE_ROWS}
          rowKey={(row) => row.id}
          sortBy={sort}
          onSortChange={(id) =>
            setSort((prev) => ({ id, direction: prev.id === id && prev.direction === 'asc' ? 'desc' : 'asc' }))
          }
        />
      </Section>

      <Section title="排版">
        <div className="styleguide__stack">
          <p className="mesh-text--display-lg">展示标题 Display LG 36/44</p>
          <p className="mesh-text--display-sm">展示标题 Display SM 30/38</p>
          <p className="mesh-text--title-1">页面标题 Title 1 24/32</p>
          <p className="mesh-text--title-2">对象标题 Title 2 20/28</p>
          <p className="mesh-text--title-3">分区标题 Title 3 18/26</p>
          <p className="mesh-text--body-lg">长文本 Body LG 16/26:中文与 English、数字 123 混排示例。</p>
          <p className="mesh-text--body">正文 Body 14/22:默认 UI 正文层级。</p>
          <p className="mesh-text--body-strong">行标题 Body Strong 14/22</p>
          <p className="mesh-text--body-sm">辅助信息 Body SM 13/20</p>
          <p className="mesh-text--caption">元数据 Caption 12/18</p>
          <p className="mesh-text--micro">状态标签 Micro 11/16</p>
          <p className="styleguide__mono">等宽 Mono:commit a1b2c3d、MES-123、0123456789</p>
        </div>
      </Section>
    </div>
  );
}
