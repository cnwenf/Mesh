# 第三方设计资产许可白名单

> **权威**：MES-135 交付物。阶段一安全与合规基线，供 S1-A 设计 Spec 与阶段二原创实现直接采用，阶段三 S3-C 审计以此为依据。
>
> 基线代码：`main`（2026-07-30）。

---

## 0. 许可分档标准

| 档位 | 含义 |
|------|------|
| ✅ 允许 | 宽松许可（MIT / ISC / Apache-2.0 / OFL-1.1 / BSD），商用无障碍，保留署名即可 |
| 🟡 有条件允许 | 可用但须满足附加条件（署名文件、动态链接、不得用其品牌背书等），条件列于备注 |
| ❌ 禁止 | 传染性许可（GPL / AGPL / SSPL）、来源不明素材、参考产品专有资产——一律不得引入 |

**通用规则**：
- 所有引入的库须在 `package.json`（或等价清单）中锁定版本（lockfile 提交）；
- 署名要求统一收归 `frontend/THIRD_PARTY_NOTICES.md`（阶段二建立）；
- 任何新增依赖合入前须经 `npm audit` + 许可扫描（见 clean-room-rules.md §3）通过；
- 参考产品自身的 UI 代码、图标、品牌资产在任何许可解释下均**禁止**引入。

---

## 1. UI 原语与组件库

| 库 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|----|------|------|------|-----------|------|
| Radix UI Primitives | MIT | ✅ | 保留 LICENSE | 无样式、无品牌绑定；headless 原语，与自建设计系统天然兼容 | ✅ 允许 |
| Headless UI (Tailwind Labs) | MIT | ✅ | 保留 LICENSE | 同上，headless；与 Tailwind 生态绑定较紧但可独立使用 | ✅ 允许 |
| React Aria (Adobe) | Apache-2.0 | ✅ | 保留 NOTICE | 无障碍能力最完整的 headless 库；Apache-2.0 含专利授权 | ✅ 允许 |
| shadcn/ui（源码复制模式） | MIT | ✅ | 保留 LICENSE | 代码复制到项目内，等同自有代码；须保留原始 MIT 头 | ✅ 允许 |
| MUI (Material UI) | MIT | ✅ | 保留 LICENSE | 视觉风格强烈（Material Design），与对标方向不符；许可本身无碍 | 🟡 有条件允许（仅限其 Base UI / 无样式层；整组件引入须确认视觉不冲突） |
| Ant Design | MIT | ✅ | 保留 LICENSE | 同上，视觉强绑定；许可无碍 | 🟡 有条件允许（同上） |
| Chakra UI | MIT | ✅ | 保留 LICENSE | 许可无碍；v3 架构变化较大，锁定版本 | 🟡 有条件允许 |
| 参考产品 UI 组件/源码 | 专有（修改版 Apache-2.0 + 额外限制） | ❌ | — | **明确禁止**，任何复制/改写/搬运均构成侵权 | ❌ 禁止 |
| 任何 GPL/AGPL 组件库 | GPL/AGPL | ❌（传染性） | — | 传染性许可，引入后整个前端须开源，商业产品不可接受 | ❌ 禁止 |

**推荐**：Radix UI Primitives（headless 原语 + 自建设计令牌，完全掌控视觉）。

---

## 2. 图标库

| 库 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|----|------|------|------|-----------|------|
| Lucide | ISC | ✅ | 保留 LICENSE | 社区驱动、风格中性、SVG 按需引入；与对标方向兼容 | ✅ 允许 |
| Phosphor Icons | MIT | ✅ | 保留 LICENSE | 6 种粗细，风格中性；React 包 `@phosphor-icons/react` MIT | ✅ 允许 |
| Tabler Icons | MIT | ✅ | 保留 LICENSE | 5000+ 图标，MIT；风格中性 | ✅ 允许 |
| Heroicons (Tailwind Labs) | MIT | ✅ | 保留 LICENSE | 与 Headless UI 同生态；数量较少（~300） | ✅ 允许 |
| Font Awesome Free | CC BY 4.0 (图标) + MIT (代码) | ✅ | **须署名**（CC BY） | 免费版部分图标 CC BY 须可见署名；Pro 图标禁止使用 | 🟡 有条件允许（仅 Free 图标 + 可见署名） |
| 参考产品图标/插画 | 专有 | ❌ | — | **禁止**——创意素材受版权保护，不得复制或"重绘相似" | ❌ 禁止 |
| 来源不明的图标包 | 不明 | ❌ | — | 无法确认许可即视为禁止 | ❌ 禁止 |

**推荐**：Lucide（ISC、按需 SVG、风格中性、社区活跃）。

---

## 3. 字体

### 3.1 英文 / UI 字体

| 字体 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|------|------|------|------|-----------|------|
| Inter | OFL-1.1 | ✅ | 保留 SIL 声明 | 专为 UI 设计，可变字重；与对标方向高度吻合 | ✅ 允许 |
| IBM Plex Sans | OFL-1.1 | ✅ | 保留 SIL 声明 | 风格中性偏技术；OFL 无传染 | ✅ 允许 |
| Geist (Vercel) | OFL-1.1 | ✅ | 保留 SIL 声明 | 现代 UI 字体；OFL | ✅ 允许 |
| SF Pro / Segoe UI / Helvetica | 专有（随 OS） | 🟡 仅限系统渲染 | — | 不得打包分发；仅可写 CSS `font-family` 回退 | 🟡 有条件允许（仅系统回退，不打包） |

### 3.2 中文字体

| 字体 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|------|------|------|------|-----------|------|
| Noto Sans SC (Google/Adobe) | OFL-1.1 | ✅ | 保留 SIL 声明 | 覆盖 GB2312/GBK；体积大（建议子集化或 CDN 按需加载） | ✅ 允许 |
| 思源黑体 (Source Han Sans) | OFL-1.1 | ✅ | 保留 SIL 声明 | 与 Noto Sans SC 同源；OFL | ✅ 允许 |
| MiSans (小米) | 小米字体许可（免费商用） | ✅ | 无需署名 | 许可明确允许商用；注意不得单独售卖字体文件 | ✅ 允许 |
| 方正/汉仪/华文系列 | 专有 | ❌（多数须购买授权） | — | 侵权风险极高，司法判例众多 | ❌ 禁止 |

### 3.3 等宽字体（代码块）

| 字体 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|------|------|------|------|-----------|------|
| JetBrains Mono | OFL-1.1 | ✅ | 保留 SIL 声明 | 专为代码设计，含连字；OFL | ✅ 允许 |
| Fira Code | OFL-1.1 | ✅ | 保留 SIL 声明 | 连字丰富；OFL | ✅ 允许 |
| ui-monospace / SF Mono / Consolas | 专有（随 OS） | 🟡 仅限系统渲染 | — | 不得打包分发；仅 CSS 回退 | 🟡 有条件允许（仅系统回退） |

**推荐组合**：
- 英文 UI：Inter（OFL-1.1，可变字重）
- 中文：Noto Sans SC（OFL-1.1，子集化按需加载）
- 等宽：JetBrains Mono（OFL-1.1）

---

## 4. 动效库

| 库 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|----|------|------|------|-----------|------|
| Framer Motion | MIT | ✅ | 保留 LICENSE | React 生态最成熟动效库；MIT | ✅ 允许 |
| react-spring | MIT | ✅ | 保留 LICENSE | 物理弹簧动效；MIT | ✅ 允许 |
| Motion One (motion.dev) | MIT | ✅ | 保留 LICENSE | 轻量、基于 Web Animations API | ✅ 允许 |
| GSAP | 免费商用（标准版） | ✅ | 无需署名 | 标准版免费商用；**不得用于竞品/竞品工具**（其许可限制条款）——Mesh 非竞品工具，可用；但许可非开源（非 MIT/Apache） | 🟡 有条件允许（标准版；确认不触发其竞品限制条款） |
| Lottie Web | MIT | ✅ | 保留 LICENSE | 播放 AE 导出动画；MIT；注意 JSON 动画文件本身的版权 | ✅ 允许 |

**推荐**：Framer Motion（MIT、React 声明式、与 Radix 兼容好）。

---

## 5. 图表库

| 库 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|----|------|------|------|-----------|------|
| Recharts | MIT | ✅ | 保留 LICENSE | 基于 D3 + React；MIT | ✅ 允许 |
| Apache ECharts | Apache-2.0 | ✅ | 保留 NOTICE | 功能最全；Apache-2.0 含专利授权 | ✅ 允许 |
| Chart.js | MIT | ✅ | 保留 LICENSE | 轻量；MIT | ✅ 允许 |
| D3.js | ISC | ✅ | 保留 LICENSE | 底层库；ISC | ✅ 允许 |
| Highcharts | 商用专有（非商用免费） | 🟡 商用须购买 | — | 商用许可费用；免费仅限非商业 | 🟡 有条件允许（须购买商用许可） |

**推荐**：Recharts（MIT、React 原生声明式）；复杂场景备选 ECharts（Apache-2.0）。

---

## 6. 样式方案

| 方案 | 许可 | 商用 | 署名 | 风险/备注 | 结论 |
|------|------|------|------|-----------|------|
| CSS Modules（Vite 内置） | MIT (Vite) | ✅ | — | 零额外依赖；项目已使用 | ✅ 允许 |
| CSS 自定义属性 / 设计令牌 | — | — | — | 原生 CSS；项目已使用（tokenValues.ts） | ✅ 允许 |
| Tailwind CSS | MIT | ✅ | 保留 LICENSE | v4 MIT；实用类优先，与设计令牌体系需桥接 | ✅ 允许 |
| vanilla-extract | MIT | ✅ | 保留 LICENSE | 编译时 CSS-in-JS；类型安全 | ✅ 允许 |
| styled-components | MIT | ✅ | 保留 LICENSE | 运行时 CSS-in-JS；MIT；运行时开销 | ✅ 允许 |
| PostCSS / Autoprefixer | MIT | ✅ | — | 工具链，无许可风险 | ✅ 允许 |

**推荐**：维持现有方案（CSS 自定义属性设计令牌 + CSS Modules），零新增依赖。

---

## 7. 推荐组合（供 S1-A 与程序员直接采用）

| 类别 | 推荐 | 许可 | 理由 |
|------|------|------|------|
| 组件原语 | **Radix UI Primitives** | MIT | headless、无视觉绑定、无障碍完善 |
| 图标 | **Lucide** | ISC | 风格中性、按需 SVG、社区活跃 |
| 英文字体 | **Inter** | OFL-1.1 | UI 专用、可变字重、与对标方向吻合 |
| 中文字体 | **Noto Sans SC** | OFL-1.1 | 全覆盖、OFL、子集化成熟 |
| 等宽字体 | **JetBrains Mono** | OFL-1.1 | 代码专用、连字、OFL |
| 动效 | **Framer Motion** | MIT | React 声明式、生态成熟 |
| 图表 | **Recharts** | MIT | React 原生、轻量 |
| 样式 | **CSS 自定义属性 + CSS Modules**（现有） | MIT (Vite) | 零新增依赖、已验证 |

---

## 8. 禁止清单（绝对红线）

1. **参考产品**的任何源代码、组件、样式文件、图标、插画、LOGO、品牌字体。
2. **GPL / AGPL / SSPL / EUPL** 许可的任何前端库（传染性，商业产品不可接受）。
3. **来源不明**的素材包（无明确许可声明的图标/字体/插画）。
4. **方正/汉仪/华文**等专有商业字体（除非单独购买书面授权并存档）。
5. 从参考产品"翻译/改写/搬运"后的任何衍生代码——即使改了变量名也构成侵权。

---

## 9. 新增依赖准入流程

任何新增前端依赖合入主干前须通过：

```bash
# 1. 许可检查（白名单模式）
npx license-checker --summary --onlyAllow "MIT;ISC;Apache-2.0;OFL-1.1;BSD-2-Clause;BSD-3-Clause;CC-BY-4.0;CC0-1.0"

# 2. 已知漏洞检查
npm audit --audit-level=high

# 3. 确认 package-lock.json 已更新并提交
git diff --stat package-lock.json
```

PR 描述中须注明：库名、版本、许可类型、引入理由。
