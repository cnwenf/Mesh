# Clean-room 红线与自检规则

> **权威**：MES-135 交付物。定义 Mesh 前端原创实现的合规红线与可复用自检手段。
> 阶段二实现须遵循本规则；阶段三 S3-C 残留审计直接复用 §4 自检脚本。
>
> 基线代码：`main`（2026-07-30）。

---

## 1. 红线定义（不得触碰）

### 1.1 源代码

| 编号 | 红线 | 说明 |
|------|------|------|
| R1 | 禁止读取参考产品仓库源代码后"翻译/改写/搬运" | 包括但不限于：逐行翻译变量名、复制 CSS 后改选择器、搬运组件结构后重命名。即使最终代码不同，过程中接触过源码即污染 clean-room 隔离 |
| R2 | 禁止复制参考产品的任何代码片段 | 包括函数、组件、CSS 规则、配置文件片段、测试用例 |
| R3 | 禁止 fork / clone / npm install 参考产品的任何包 | 不得将其包作为依赖、devDependency 或本地参考引入开发环境 |
| R4 | 禁止在 IDE / 编辑器中打开参考产品源码作为"对照" | clean-room 要求：看设计（截图/视觉）写代码，不看实现 |

### 1.2 创意素材

| 编号 | 红线 | 说明 |
|------|------|------|
| R5 | 禁止使用参考产品的图标 / 插画 / LOGO / 图片 | 不得复制、重绘相似版本、或做"微调"后使用 |
| R6 | 禁止使用参考产品的品牌名 / 署名 / 域名 | 代码、注释、文档、提交信息、分支名中一律不出现 |
| R7 | 参考产品截图不得入代码仓库 | 设计调研截图仅存于 `docs/research/`（git-ignored 或独立私有存储），不进 `src/`、`public/`、`docs/specs/` |

### 1.3 提交与分支

| 编号 | 红线 | 说明 |
|------|------|------|
| R8 | 提交信息不得出现参考产品名 / 域名 / 代号 | 使用"参考产品"或"竞品"等脱敏称呼 |
| R9 | 分支名不得出现参考产品名 | 使用 issue 编号（如 `agent/mesh/xxx`、`feat/MES-130`） |
| R10 | 代码注释 / TODO / FIXME 不得出现参考产品名 | 同上 |

---

## 2. 允许的行为

| 行为 | 条件 |
|------|------|
| 观察参考产品**运行态 UI**（浏览器截图）提取设计规范 | 截图不入库；仅提取色值/间距/字阶/布局等不受版权保护的设计事实 |
| 参考公开设计文章/设计系统文档（如 Material Design、Apple HIG） | 这些是公开设计语言，非参考产品专有 |
| 使用与参考产品**相同的第三方库**（如 Inter 字体、Lucide 图标） | 按各自许可独立引入，不从参考产品仓库复制 |
| 实现相同的交互模式/信息架构 | 交互模式与设计理念不受版权保护 |
| 参考公开 W3C/MDN 文档实现 Web 标准功能 | 公共知识 |

---

## 3. 依赖许可扫描命令

### 3.1 前端依赖许可检查（白名单模式）

```bash
cd frontend

# 安装扫描工具（一次性）
npm install -D license-checker

# 扫描：仅允许以下宽松许可
npx license-checker --excludePrivatePackages --summary --onlyAllow \
  "MIT;ISC;Apache-2.0;OFL-1.1;BSD-2-Clause;BSD-3-Clause;CC-BY-4.0;CC0-1.0;Unlicense;0BSD;Python-2.0;MIT-0;BlueOak-1.0.0;MPL-2.0"

# 详细输出（排查用）
npx license-checker --csv --out licenses.csv
```

`--excludePrivatePackages` 只排除仓库自身的 `private: true` 根包，不排除任何第三方依赖。MPL-2.0 仅用于当前 axe/lightningcss 等未改写依赖的文件级弱 copyleft 场景，须保留其许可且不得复制修改后的 MPL 文件而不公开对应文件源码。

**不通过即阻断合入**：任何 GPL / AGPL / LGPL / SSPL / EUPL / 专有许可的包均不得进入依赖树。

### 3.2 已知漏洞检查

```bash
# 高危及以上漏洞必须清零
npm audit --audit-level=high

# 或仅报告不修复（CI 用）
npm audit --json | python3 -c "
import sys, json
d = json.load(sys.stdin)
vulns = d.get('vulnerabilities', {})
high = [k for k, v in vulns.items() if v.get('severity') in ('high', 'critical')]
if high:
    print(f'BLOCKED: {len(high)} high/critical vulnerabilities: {high}')
    sys.exit(1)
print('PASS: no high/critical vulnerabilities')
"
```

### 3.3 lockfile 完整性

```bash
# 确认 lockfile 与 package.json 同步
npm ci --dry-run
# 若有 diff 则说明 lockfile 过期——必须 npm install 后重新提交
```

### 3.4 Appica UI 底座专项门禁（MES-158）

```bash
cd frontend
npm run check:appica
```

该门禁 fail closed 校验 `@appica/ui-react@1.0.0` 与 Tailwind 构建依赖均为精确版本、实际安装包为 MIT、`THIRD_PARTY_NOTICES.md` 含完整声明、样式与 token 桥接入口存在，并拒绝根 barrel import。其结果不能替代 §3.1 全依赖树许可扫描或 §3.2 漏洞审计；三者均须通过。

---

## 4. 自动化残留审计

`mesh.compliance.source_provenance` 是唯一自动化入口，扫描 `git ls-files` 返回的全部
受管文本、完整提交信息/作者以及所有 refs。扫描器只输出规则编号与文件/行号，绝不把
匹配规则或命中原文复制到日志。

### 4.1 规则必须从仓库外注入

匹配词表不得出现在代码、注释、文档、提交信息或 refs 中。CI 必须通过受控 repository
Actions secret `MESH_FORBIDDEN_SOURCE_PATTERNS` 注入换行分隔的正则；本地审核使用位于仓库
外、权限受控的规则文件：

```bash
PYTHONPATH=backend/src python -m mesh.compliance.source_provenance \
  --root . --patterns-file /secure/path/source-provenance-patterns.txt
```

缺少外部规则、规则为空、正则非法或 Git 元数据不可读时均返回退出码 `2`，不得跳过；
发现残留返回 `1`；全部受管文本、提交信息和 refs 零命中才返回 `0`。规则文件与 secret
均不得写入仓库、构建产物或 CI artifact。

### 4.2 扫描边界

- 当前版本：扫描全部 Git 受管 UTF-8 文本；二进制文件不作字符串推断，仍走 §4.3 素材来源核查。
- Git 元数据：`fetch-depth: 0` 后扫描完整 commit message/author 与 `refs/*`。
- 日志脱敏：诊断仅含 `{source, line, rule}`，禁止输出命中行、规则正文或上下文。
- 任何变更：`.github/workflows/source-provenance.yml` 在 push 与 pull request 上 fail closed 执行。

### 4.3 素材来源核查步骤

对 `frontend/public/`、`frontend/src/**/assets/`、`docs/` 下的所有图片/SVG/字体文件逐一核查：

```bash
#!/usr/bin/env bash
# clean-room-assets.sh — 列出所有静态素材，供人工核查来源
# 用法: ./scripts/clean-room-assets.sh

set -euo pipefail

echo "=== 静态素材清单（人工核查来源）==="
echo ""

# 图片
echo "--- 图片文件 ---"
find frontend/public frontend/src -type f \
  \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.gif" \
     -o -name "*.webp" -o -name "*.svg" -o -name "*.ico" \) \
  ! -path "*/node_modules/*" 2>/dev/null | sort

echo ""
echo "--- 字体文件 ---"
find frontend/public frontend/src -type f \
  \( -name "*.woff" -o -name "*.woff2" -o -name "*.ttf" -o -name "*.otf" \) \
  ! -path "*/node_modules/*" 2>/dev/null | sort

echo ""
echo "--- 核查要求 ---"
echo "每个文件须能回答：来源是什么？许可是什么？署名是否已保留？"
echo "无法回答来源的文件 = 不合规，须删除或替换。"
```

---

## 5. CI 集成

仓库门禁 `.github/workflows/source-provenance.yml` 采用以下契约：

```yaml
repository-audit:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - name: 来源审计
      env:
        MESH_FORBIDDEN_SOURCE_PATTERNS: ${{ secrets.MESH_FORBIDDEN_SOURCE_PATTERNS }}
      run: PYTHONPATH=backend/src python -m mesh.compliance.source_provenance --root .
```

依赖许可与漏洞检查仍按 §3 独立执行，不与来源匹配规则混用。

---

## 6. 违规处置

| 严重度 | 情形 | 处置 |
|--------|------|------|
| CRITICAL | 参考产品源代码/素材进入仓库 | 立即 revert + `git filter-branch` / BFG 清除历史；安全审核不通过，阻断合入 |
| CRITICAL | 提交信息/分支名含参考产品真名 | 立即 amend/rebase 清除；已 push 则 force-push 清理 |
| HIGH | 传染性许可（GPL/AGPL）依赖进入 | 立即移除依赖 + 评估是否需替换已实现功能 |
| MEDIUM | 素材来源不明（无法确认许可） | 删除或替换为白名单内资产 |
| LOW | 注释中出现"竞品"以外的暗示性措辞 | 修改为脱敏表述 |

---

## 7. 术语约定

- **参考产品**：本文件中对对标产品的唯一称呼。所有产出物（代码/文档/提交/分支）中一律使用此称呼或"竞品"，**绝不使用其真实名称**。
- **Clean-room**：隔离实现方法——仅基于设计观察（运行态 UI 截图）编写代码，不接触原始实现。
- **残留**：任何指向参考产品真实身份的字符串、文件、素材、提交记录。
