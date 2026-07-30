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
npx license-checker --summary --onlyAllow \
  "MIT;ISC;Apache-2.0;OFL-1.1;BSD-2-Clause;BSD-3-Clause;CC-BY-4.0;CC0-1.0;Unlicense;0BSD;Python-2.0"

# 详细输出（排查用）
npx license-checker --csv --out licenses.csv
```

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

---

## 4. 自检脚本（S3-C 残留审计复用）

以下脚本可在 CI 或本地一键执行，检测代码仓库中是否存在参考产品残留。

### 4.1 敏感词 / 域名 / 署名 grep

```bash
#!/usr/bin/env bash
# clean-room-grep.sh — 扫描仓库中是否存在参考产品残留
# 用法: ./scripts/clean-room-grep.sh [目录，默认仓库根]
# 退出码: 0=通过, 1=发现残留

set -euo pipefail
ROOT="${1:-.}"
VIOLATIONS=0

# ─── 敏感词表（小写匹配）───
# 注意：此表本身不得包含参考产品真名——用正则模式匹配
SENSITIVE_PATTERNS=(
  # 参考产品品牌名（按实际脱敏需要维护，此处为模式占位）
  "multica"
  "mlt\.dev"
  "multica\.ai"
  "multica\.com"
  "multica\.io"
  # 参考产品 GitHub org / 仓库路径
  "github\.com/multica"
  # 参考产品 npm 包名前缀
  "@multica/"
  # 常见代号/别名（按需追加）
  "the-reference-product"
)

echo "=== Clean-room 敏感词扫描 ==="
echo "扫描目录: $ROOT"
echo ""

for pattern in "${SENSITIVE_PATTERNS[@]}"; do
  # 排除本文件自身、git 目录、node_modules、lockfile
  HITS=$(grep -rnil "$pattern" "$ROOT" \
    --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" \
    --include="*.css" --include="*.scss" --include="*.html" --include="*.json" \
    --include="*.md" --include="*.yml" --include="*.yaml" --include="*.toml" \
    --include="*.py" --include="*.sh" --include="*.env*" \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=dist \
    --exclude="package-lock.json" --exclude="clean-room-grep.sh" \
    2>/dev/null || true)

  if [ -n "$HITS" ]; then
    echo "❌ 发现匹配 '$pattern':"
    echo "$HITS" | while read -r f; do echo "   $f"; done
    VIOLATIONS=$((VIOLATIONS + 1))
  fi
done

echo ""
if [ "$VIOLATIONS" -gt 0 ]; then
  echo "❌ 扫描未通过：发现 $VIOLATIONS 类敏感词残留，须逐一排查清理。"
  exit 1
else
  echo "✅ 扫描通过：未发现参考产品残留。"
  exit 0
fi
```

### 4.2 提交历史扫描

```bash
#!/usr/bin/env bash
# clean-room-git-log.sh — 扫描提交信息/作者中的参考产品残留
# 用法: ./scripts/clean-room-git-log.sh [起始ref，默认全量]

set -euo pipefail
REF="${1:-}"
VIOLATIONS=0

PATTERNS="multica|mlt\.dev|multica\.ai|multica\.com|@multica/"

echo "=== 提交历史扫描 ==="

if [ -n "$REF" ]; then
  LOG_CMD="git log $REF..HEAD --format=%H|%s|%an|%ae"
else
  LOG_CMD="git log --format=%H|%s|%an|%ae"
fi

HITS=$($LOG_CMD | grep -iE "$PATTERNS" || true)

if [ -n "$HITS" ]; then
  echo "❌ 提交历史中发现敏感词:"
  echo "$HITS" | head -20
  VIOLATIONS=1
fi

# 检查分支名
BRANCH_HITS=$(git branch -a | grep -iE "$PATTERNS" || true)
if [ -n "$BRANCH_HITS" ]; then
  echo "❌ 分支名中发现敏感词:"
  echo "$BRANCH_HITS"
  VIOLATIONS=1
fi

echo ""
if [ "$VIOLATIONS" -gt 0 ]; then
  echo "❌ 扫描未通过。"
  exit 1
else
  echo "✅ 提交历史与分支名无残留。"
  exit 0
fi
```

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

## 5. CI 集成建议

在 PR 检查流水线中加入：

```yaml
# .github/workflows/clean-room.yml（示意）
clean-room-check:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0  # 全量历史
    - name: 敏感词扫描
      run: bash scripts/clean-room-grep.sh .
    - name: 提交历史扫描
      run: bash scripts/clean-room-git-log.sh origin/main
    - name: 依赖许可扫描
      run: cd frontend && npm ci && npx license-checker --summary --onlyAllow "MIT;ISC;Apache-2.0;OFL-1.1;BSD-2-Clause;BSD-3-Clause;CC-BY-4.0;CC0-1.0"
    - name: 漏洞检查
      run: cd frontend && npm audit --audit-level=high
```

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
