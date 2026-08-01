# MES-128 响应式、可访问性与门禁收尾实施计划

## 目标

在不改变业务语义的前提下，把 `design-quality.md` §8、§10.2、§13.5 的跨切面
契约落实为可复现的代码、静态门禁、浏览器验证和存证。

## 实施顺序

1. 建立断点单一事实源与静态扫描，先让现有 359/720/800/899/900 等近似值失败。
2. 统一 compact/medium/wide/xwide 边界；medium 外壳固定折叠 rail，业务双栏改用
   container query；补齐 safe-area 与 coarse pointer 44px 命中区。
3. 建立结构级无障碍扫描，先暴露业务自造模态框及缺 caption/scope 的表格。
4. 复用共享 Dialog/Drawer 焦点圈养，清偿原生表格语义；补唯一 h1、live region、
   看板非拖拽公告的浏览器断言。
5. 以 production preview + 新鲜 mock 进程运行 390/768/1024/1440、亮暗、核心路由
   的 axe/键盘/overflow/视觉矩阵；另跑 320 CSS px 重排。
6. 记录旧 token 别名预算和页面状态缺口；只做小修，大缺口留给父 Issue 的 Stage 3。
7. 全量运行 lint、typecheck、build、contrast、coverage、e2e、visual 与证据唯一性门禁。

## 回滚与风险控制

- 响应式修改限定为布局规则和共享原语，不改 API、路由或持久化语义。
- 视觉基线只在确定性生产预览中更新，截图差异逐页人工核对。
- 静态扫描采用预算式迁移：允许旧别名减少，禁止新增；一个发布周期后另 Issue 删除。
