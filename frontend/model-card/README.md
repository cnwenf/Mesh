# MES-108 React 迁移模型卡

`mes108-react-migration.json` 是静态原型到真实 React 前端的机器可读迁移台账。它固定原型 revision，并逐项记录页面、React 扩展页、规范与兼容路由、组件、设计令牌、状态、输入方式、测试及视觉证据。

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

最终验收使用发布门禁：

```bash
node scripts/verify-model-card.mjs --mode release
```

发布门禁会对未确认原型、`pending`、`blocked` 或缺少真实证据直接失败。不要为了通过检查改写状态；应先补齐固定环境下的真实浏览器操作、截图与差异关闭证据，再更新模型卡。

视觉状态为 `verified` 时，`artifacts` 必须逐格绑定 `viewport`、`theme`、`state` 与 `path`；每格使用 `e2e/evidence/mes108/` 下唯一的 PNG/WebP 截图。`not-applicable` 只能用于页面状态本身明确标为 `not-applicable` 的非默认格，并必须说明原因。交互状态为 `verified` 时，证据必须绑定 `e2e/**/*.spec.ts`、真实存在的测试标题及其覆盖的输入方式，且完整覆盖该交互声明的输入方式。

canonical 路由、别名 query、旧路由目标和共享组件清单会与源码 inventory 做 fail-closed 核对。JSX 相对路由的父级契约由验证器中的显式表维护；自动校验确认 path 字面量、canonical 路径和组件文件存在，但不推断 `<Route element>` 与 import symbol，因此任何路由元素或嵌套层级改动仍须在代码评审中逐项核对模型卡。

原型 revision 必须已存在于本地 Git 对象库。CI 会显式获取固定 revision，避免校验工作区中的可变文件。
