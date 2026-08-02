# MES-108 React 迁移模型卡

`mes108-react-migration.json` 是固定设计输入到真实 React 前端的机器可读迁移台账。Schema v2 将输入生命周期与 release 决策分开：MES-142 / PR #100 的固定 revision 以 `cancelled + partial-input` 保留，既不丢失可复核输入，也不把取消误当成产品批准。模型卡逐项记录页面、React 扩展页、规范与兼容路由、组件、设计令牌、状态、输入方式、测试及视觉证据。

本门禁仅验证模型卡台账的结构、迁移状态与证据绑定完整性，不承担 clean-room 来源与品牌红线扫描；后者由仓库级 `.github/workflows/source-provenance.yml` 独立、fail-closed 地把守。

结构审计允许诚实保留 `pending`，用于在迁移期间阻止清单和源码漂移：

```bash
node scripts/verify-model-card.mjs --mode audit
```

更新 JSON 后，重新生成评审文档并复查：

```bash
node scripts/verify-model-card.mjs --mode audit --write
node scripts/verify-model-card.mjs --mode audit
```

最终验收使用发布门禁。批准文件不得提交到仓库；CI 从当前 PR 上仓库 owner 的精确决策评论生成临时 attestation，并把它与 head SHA、模型卡 SHA-256 及固定输入 revision 绑定。PR #120 由仓库 owner 创建，GitHub 不允许作者审核自己的 PR，因此这里使用可审计的 PR 评论而不是自审 review；只有 `author_association=OWNER` 的评论才有效。

CI 的失败日志会输出要由 owner 原样发布到 PR 的单行决策文本；格式如下。任何 push 或模型卡修改都会让旧评论失效。评论发布后须重新运行失败的 release job，CI 才会读取该决策：

```text
/mes108-release approve head=<current-pr-head-sha> card=<model-card-sha256> baseline=<fixed-input-revision>
```

```bash
node scripts/verify-model-card.mjs \
  --mode release \
  --approval-file /trusted/runtime/mes108-release-approval.json \
  --evidence-run-file /trusted/runtime/mes108-runtime-evidence.json \
  --release-head <current-pr-head-sha> \
  --release-repository cnwenf/Mesh \
  --release-owner cnwenf
```

发布门禁会对缺少当前 head 的 owner 批准、`pending`、`blocked` 或缺少真实证据直接失败。模型卡内的 `confirmed` / `approved` 字段不能代替外部批准，旧 `blueprint.confirmed` 和内联 `releaseApproval` 会被拒绝。不要为了通过检查改写状态；应先补齐固定环境下的真实浏览器操作、截图与差异关闭证据，再更新模型卡。

release 摘要会同时报告未决记录与实际视觉单元数。当前 144 个未决记录由 28 个 reconciliation、6 个状态、30 个交互、28 个视觉证据组（展开为 412 个适用像素单元）、5 个组件、42 个令牌及 5 个校准风险组成；不能把 28 个视觉组误报成 28 张截图。

视觉状态为 `verified` 时，`artifacts` 必须逐格绑定 `viewport`、`theme`、`state`、`path` 与文件 `sha256`；每格只能使用 `e2e/evidence/mes108/` 下路径和内容摘要均全局唯一的 PNG。验证器会解析 PNG 签名、chunk CRC、像素数据与实际尺寸，拒绝空文件、伪后缀、损坏图片及尺寸冒充。每项还必须包含：

- `capture`：`runner=playwright`、专用 `playwright.mes108.config.ts`、`phone` / `wide` 项目、真实 `e2e/**/*.spec.ts`、精确测试标题、UTC 捕获时间，以及与模型卡固定视觉环境绑定的 SHA-256；视觉 spec 必须从 `e2e/mes108-evidence-fixture.mjs` 原名导入 `test` / `expect`，且目标用例必须通过 fixture 提供的 `mes108Screenshot.capture(<claimed-path>)` 生成每个声明文件。只接受普通 `test(...)`，`skip` / `fixme` / `only` 不能作为证据，对应测试必须执行截图比较和 fixture 截图 API。
- `comparison`：独立基线路径、基线/实际 SHA-256、`algorithm=rgba-exact-v1`、视口像素总数、差异像素数、阈值与结果。验证器直接解码两张 PNG 并逐 RGBA 像素重算差异；超过阈值会失败，`approved-difference` 必须引用已关闭的校准风险并说明原因。

提交文件和 JSON 只做静态预检，不能自行证明测试已经运行。PR release job 会检出精确 head；发现任何 `verified` 交互或视觉项后，才安装 Chromium，并按 config / project / spec / 精确标题后缀逐组运行 Playwright，reporter 同时要求整个运行只有一个精确匹配用例。runner 会在运行前隔离模型卡声明的 actual PNG；自动 fixture 必然随目标测试运行，并在内部调用真实 `page.screenshot`，把返回字节的 SHA-256 与仓库相对输出路径作为一份唯一 manifest 交给 reporter。最终输出集合必须与本组 claim 精确相等，且落盘字节必须仍匹配 fixture 捕获时的摘要；把 capture 放在未执行分支会产生空 manifest，手工同名 attachment 会形成重复 manifest，两者都 fail closed。只改时间戳、截到无关路径、截图后覆盖旧字节或只导入不用 fixture 同样会失败。自定义 reporter 还只记录本次成功测试实际执行的 Playwright / expect API，随后由独立 runner 重算基线摘要与像素差异。临时运行清单绑定 repository、head SHA 和模型卡 SHA-256，再由最终验证器消费；该清单不得提交到仓库。

Playwright 完成后，CI 才重新读取当前 PR 的 owner 决策评论，写入随机命名的临时 attestation，并紧接着执行最终验证；浏览器测试不能预先覆盖批准文件。该批准文件和运行清单都只存在于 runner 临时目录。

`not-applicable` 只能用于页面状态本身明确标为 `not-applicable` 的非默认格，并必须说明原因。交互状态为 `verified` 时，证据必须绑定专用 config / project、`e2e/**/*.spec.ts` 与精确匹配的普通测试标题，并由本次运行报告中的操作 API 完整证明声明的输入方式：鼠标需要 click/drag/hover 等动作，键盘需要 press/type 等动作（`fill()` 不能冒充键盘操作），触屏需要 tap/touchscreen 动作。注释、字符串、跳过的测试或只写 `inputModes` 标签都不构成证据。

canonical 路由、别名 query、旧路由目标和共享组件清单会与源码 inventory 做 fail-closed 核对。JSX 相对路由的父级契约由验证器中的显式表维护；自动校验确认 path 字面量、canonical 路径和组件文件存在，但不推断 `<Route element>` 与 import symbol，因此任何路由元素或嵌套层级改动仍须在代码评审中逐项核对模型卡。

固定输入 revision 必须已存在于本地 Git 对象库。CI 会显式获取该 revision，避免校验工作区中的可变文件。PR 上的 release job 不再因输入未确认而成功跳过：只有精确 head 上的 Playwright 运行证据、仓库 owner 对该 head 与模型卡摘要发布的有效决策评论以及全部静态门禁同时满足，release 才会通过。
