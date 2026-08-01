# MES-111 批次④ 走查存证

设置 + 搜索/命令面板 + Analytics + /approvals 的真实页面操作存证。
由 `frontend/e2e/real-mes111-b4.spec.ts` 在真实验收栈(production 鉴权 + 公网 HTTP)上
自动生成,桌面(1440×900)+ 手机(390×844,触控)× 亮/暗四组合。

## 命名规则

`{project}-{page}-{theme}.png`

- project:`desktop` / `mobile`
- page:`settings` / `ws-settings` / `palette` / `insights-empty` / `insights-data` / `approvals`
- theme:`light` / `dark`

## 复现

```bash
# 仓库根目录:起隔离验收栈
./frontend/e2e/mes111-b4/gen-stack-env.sh
docker compose -p mes111-b4 \
  -f docker-compose.yml -f frontend/e2e/mes111-b4/compose.override.yml \
  --env-file frontend/e2e/mes111-b4/stack.env up -d --build

# frontend/ 目录:跑 e2e(每用例自带亮→暗切换走查)
npx playwright test --config playwright.mes111-b4.config.ts

# md5 唯一性门禁
node scripts/check-evidence-unique.mjs e2e/evidence/mes111-b4
```

## 覆盖矩阵

| 页面 | 桌面亮 | 桌面暗 | 手机亮 | 手机暗 |
| --- | --- | --- | --- | --- |
| 账号设置(appearance 分页) | ✅ | ✅ | ✅ | ✅ |
| 工作区设置(general + G11) | ✅ | ✅ | ✅ | ✅ |
| 命令面板(六类检索结果) | ✅ | ✅ | ✅ | ✅ |
| Analytics 空窗 | ✅ | — | ✅ | — |
| Analytics 有数据 | ✅ | ✅ | ✅ | — |
| /approvals 深链 | ✅ | ✅ | ✅ | ✅ |
