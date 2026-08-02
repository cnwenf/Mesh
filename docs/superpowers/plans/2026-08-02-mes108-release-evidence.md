# MES-108 release 证据收口计划

> 基线：PR #120 的 `2506d885`；静态输入 MES-142 已取消，固定修订仍保留为部分设计输入。
> 方法：writing-plans、test-driven-development、systematic-debugging、
> verification-before-completion、requesting-code-review。

## 1. 复现与根因

1. `audit` 当前通过；`release` 当前按设计失败。
2. CLI 所报“144 个未决项”不是 144 张截图：它由 28 个页面 reconciliation、6 个状态、
   30 个交互、28 个视觉证据组、5 个组件、42 个令牌和 5 个校准风险组成；28 个视觉组实际
   展开为 412 个适用像素单元。
3. 旧 `blueprint.confirmed=false` 忠实反映未获产品 owner 正式视觉签字。MES-142 的
   `cancelled` 生命周期只表示独立静态原型路线被取代，不能等价成确认，也不能由程序员自行
   改为 `true`；Schema v2 因此将该布尔值拆成输入生命周期、采用方式与外部 release 决策。
4. 当前模型卡把“输入 Issue 生命周期”“部分输入是否被采用”“最终 release 是否获 owner
   批准”压在一个布尔值上，且汇总错误隐藏了视觉单元规模，容易让后续运行误判工作量或绕过
   门禁。

## 2. 实施步骤

### Task A：TDD 固定基线与批准语义

- 先新增失败测试，要求模型卡显式记录输入生命周期、采用方式与独立 release 批准。
- 证明 `cancelled` / `superseded` 输入不能自动授权 release；缺失或未批准时必须 fail closed。
- 保留固定 revision 的源码库存校验，避免取消 Issue 后丢失可复核输入。

### Task B：TDD 固定未决证据分解

- 先新增失败测试，要求 release 错误给出按类别分解，并显示视觉证据组与实际像素单元数。
- 对 malformed collection、not-applicable 单元和空矩阵做负向覆盖，避免统计自身成为绕过点。
- 保持摘要有界，同时提供机器可读的准确总数。

### Task C：真实证据映射

- 逐项核对 30 个交互与真实 Playwright 用例标题；只有精确用例真实覆盖声明的输入方式，才把
  `pending` 改为 `verified`。
- 对 412 个视觉单元核对固定 Chromium、390×844 / 1440×900、亮暗主题、四状态、固定字体与
  时间；尺寸、主题或状态不匹配的历史截图不得冒充。
- reconciliation、组件、令牌与校准风险只有在对应比较或明确产品决定存在时才能推进；不能为
  让 release 变绿而批量改状态。

### Task D：文档与门禁

- 更新模型卡、生成文档、模型卡 README 和项目 README，使取消的静态输入与 owner release
  决策成为两个独立事实。
- `audit` 必须通过；`release` 若仍失败，输出必须只包含真实未决项和明确 owner 决策点。

### Task E：verification-before-completion 与 review

- 运行 Node 22 的模型卡测试及核心/CLI 分文件覆盖率，均须达到 90%。
- 运行受影响前端的 lint、typecheck、build、格式与 diff 检查；任何新增交互证据必须运行对应
  真实 E2E，而非仅检查文件存在。
- 由独立 reviewer 审查 fail-closed 语义、统计准确性、证据真实性与范围边界；处理全部 finding
  后再提交、推送并更新 PR #120。

## 3. 过程证据

- **writing-plans**：本文件先于测试与实现改动建立。
- **systematic-debugging（初始复现）**：`audit` 退出码 0；`release` 退出码 1。用模型卡自身
  数据独立统计得到 144 项分类和 412 个适用视觉单元，确认原错误摘要只展示前 12 项，未暴露
  工作量结构。
- **真实证据审计**：30 个交互中 0 个具备覆盖全部声明输入方式的可采纳证据；仓库没有触屏
  `.tap()` / `touchscreen` 路径，键盘覆盖也不足。28 个视觉组的 412 个适用单元全部仍为
  `pending`；`frontend/e2e/evidence/mes108/` 不存在，既有截图没有同时满足 zh-CN、UTC、冻结
  时间、锁定字体和禁用动画的固定环境，因此 0 张可冒充本门禁证据。
- **TDD**：先让 baseline / 外部批准 / 144+412 汇总 / CI 不跳过四组测试失败，再实现 Schema
  v2、外部 attestation 与准确汇总。发现 PR #120 由 repository owner 本人创建、GitHub 不允许
  自审后，又先将契约改成 owner PR 决策评论并复现 3 个失败测试，再改实现至通过。
- **requesting-code-review**：首轮独立 reviewer 发现仅凭路径/标签可伪造视觉与输入方式证据，
  并指出编辑后的 owner 评论若沿用 `created_at` 会留下错误决策时间。修复先以负向测试复现，
  再加入 PNG CRC/解压/尺寸与摘要校验、全局内容去重、Playwright 截图和操作源码绑定、
  `rgba-exact-v1` 逐像素门禁，同时将 attestation 时间改为 GitHub `updated_at`。复审又发现提交
  JSON 仍可能自证运行、`skip` / `fixme` 用例仍可能冒充证据；因此 release job 改为检出精确
  PR head，并由 CI 的 Playwright runner/reporter 生成仅存在于运行时的证据清单，记录真实执行
  的 API、解析后的浏览器环境、截图重写与重算摘要/像素差，所有跳过或不完整结果均 fail closed。
  最终复审继续发现浏览器测试可预先覆盖固定批准文件、精确叶标题无法命中 describe 内用例，
  以及仅改截图时间戳并对无关路径截图即可伪造“本次生成”。对应负向测试先失败后，批准解析被
  移到 Playwright 之后并使用随机临时路径；grep 改为安全标题后缀且 reporter 要求整次运行唯一
  精确用例；runner 在执行前隔离 claimed actual，专用 fixture/reporter 绑定实际
  `page.screenshot({ path })` 输出路径，缺失、无关路径和时间戳伪造均失败。定向复核又证明“只
  导入不用 fixture + 手工 attachment”仍可自报路径；新增负向复现后，静态门禁强制目标
  `test` / `expect` 原名来自 fixture，并要求逐 claim 调用 `mes108Screenshot.capture(path)`；fixture
  内部绑定真实截图返回字节 SHA，reporter/runner 要求输出路径集合精确相等且落盘摘要不变。最后
  将截图 fixture 设为 auto：未实际执行 capture 必然产生空 manifest，手工自报则产生重复
  manifest，两个绕过均 fail closed。
- **verification-before-completion**：Node 22 模型卡 52/52 测试通过；核心验证器 99.66% 行、
  90.27% 分支、100% 函数，CLI 99.06% 行、97.65% 分支、100% 函数，运行时证据 producer /
  reporter / fixture 合计 98.45% 行、92.93% 分支、95.24% 函数。模型卡 audit 通过；当前模型卡 0 个
  `verified` 声明，所以计划器正确返回 `false`，没有运行或声称真实浏览器证据。无 attestation
  的 release 按预期以 2 个错误失败（owner 决策缺失 + 144 个未决记录 / 412 个视觉单元）。
  完整前端覆盖率门禁 380 文件、4,144 测试全绿，整体 98.18% 行/语句、92.77% 分支、96.22%
  函数，213 个受控源文件的逐文件 90% 门禁通过。生产构建及受影响文件 ESLint / Prettier /
  YAML 语法 / diff 检查通过。全量 `npm run format:check` 仍被仓库既有 441 个未格式化文件阻塞，
  本次相关文件单独检查全绿，未批量改写无关代码。
- **技能加载说明**：已请求并获确认安装 Superpowers 插件，但当前活动运行未热加载其
  `SKILL.md` 资源。因此本次按上述五类合同实际执行并在本文记录真实时序，不把未发生的技能
  调用记为已发生。
