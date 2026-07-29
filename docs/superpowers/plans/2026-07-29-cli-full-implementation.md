# 开发者平台 CLI 全功能实现 实施计划(MES-80,阶段9·平台能力)

> **归档性质:** 本计划按 superpowers:writing-plans 编写并随实现归档。Issue 写死「CLI 语言/框架由程序员选型(建议与后端 OpenAPI 契约测试协同便利),选型写入 writing-plans 归档」——选型理由见「技术选型」节。实施全程使用 superpower skills:writing-plans(本文档)→ test-driven-development → systematic-debugging(真栈缺陷定位)→ verification-before-completion。

**Goal:** 按 `docs/specs/features/cli.md` 五章全功能实现 `mesh` 命令行(REST 瘦客户端),含 Spec 声明的 auth.md 设备码增量(owner 为 auth.md,本 Issue 一并落地)与 OpenAPI 3.1 公开契约 + 签名分发(§5.4)。

**Architecture:** CLI 是与 Web 同源 `/api/v1` 的瘦客户端——不含业务逻辑,只做请求构造、凭证管理、输出纪律三层。认证走设备码(RFC 8628,批准页绑定工作区即 CLI 默认)或 PAT(stdin,绝不进 argv);会话凭证经 §3.8 有界幂等 refresh 轮换续签(单飞 + 宽限重读)。auth 增量落在后端:`device_authorizations` 状态机(HMAC-SHA256 服务端 pepper 仅存哈希)+ 确认/拒绝/轮询端点(名册 `FOR UPDATE` 固定锁序 + scope 服务端取交)+ 会话绑定列 + 统一 Bearer 依赖(前缀路由 JWT/`mesh_pat_`/`mesh_agt_`)。公开契约 `docs/api/openapi.yaml` 由 FastAPI 生成后**整体剔除** `/api/v1/daemon/*`(含孤儿 schema 回收),CI 门禁断言零命中 + CLI 依赖端点齐全。

## 技术选型(Issue 写死归档项)

| 维度 | 选型 | 理由 | 否决的备选 |
|---|---|---|---|
| 语言 | **Python 3.12** | ① 与后端同语言——契约测试可直接共享后端的包络/OpenAPI 产物与 pytest 基建(「与后端 OpenAPI 契约测试协同便利」的直接解);② 开发者本机已有 Python(后端要求),零新运行时;③ 团队技术栈统一,维护面单一 | Go(单二进制分发占优,但引入第二工具链,契约测试需经 HTTP 黑盒协同,与 Issue 建议相悖);Node(pkg 打包体积大、启动慢);Rust(团队无存量,投入产出失衡) |
| 命令框架 | **click ≥8.1** | 三层 help 原生支持;`standalone_mode=False` 入口可精确实现退码契约(0/1/2/3/4/130,用法错归 3 不占鉴权专属 2);补全脚本生成成熟 | argparse(退码/帮助层级手工量大);typer(多一层抽象,click 直通更可控) |
| HTTP 客户端 | **httpx ≥0.27** | 同步客户端 + 流式(SSE 降级通道 `iter_lines`);传输层可注入 → respx 全量 mock 单测;TLS 校验/自定义 CA/代理(带认证)一等公民,支撑传输 fail-closed 与 `--insecure` 单次旗标语义 | requests(流式/传输注入弱);urllib(代理/CA 手工量大) |
| 配置/凭证 | **PyYAML ≥6.0** + 自研 0600 fail-closed 存储 | 两份文件物理分离(config / credentials);原子写(临时文件 0600 → fsync → rename)+ 属主/符号链接/过宽权限拒载 | TOML(生态可,但凭证文件自研校验与格式无关,YAML 与后端/编排一致) |
| 分发 | **PyInstaller 单二进制** + minisign 签名 + SHA-256 | §5.4 要求多平台单二进制 + 校验和 + 签名;PyInstaller 四平台原生 runner 构建(不交叉编译),`cli-release.yml` 自动化;install.sh 先校验和后验签(公钥随仓库,与 runtime.md 安装包同基线),不鼓励盲管道 | zipapp(仍需目标机 Python,不满足「单二进制」);Go 重写(见语言节) |

## Global Constraints

- **Spec 唯一权威**:cli.md 五章 + auth.md §2.4.2/§3.1.1/§3.8;契约(错误信封/包络/游标)对齐 `docs/specs/README.md` §6.14。
- **退码契约(表驱动)**:0 成功 / 1 通用(5xx·网络·限流耗尽)/ 2 鉴权专属(401/403·未登录·过期·吊销)/ 3 校验(400/404/422·用法错误·未知命令)/ 4 冲突(409/423)/ 130 中断;单一表驱动测试逐行断言,不手写散例。
- **输出纪律**:`--output json` 时 stdout 恰为单一合法 JSON;进度/告警/错误/verbose 一律 stderr;`--jq` 内置 jq 子集(不依赖外部 jq)。
- **凭证安全**:令牌不落 argv/历史/进程表(无 `--token` flag,`device_code` 不打印);凭证文件 0600/父目录 0700 fail-closed(过宽拒载 + 拒符号链接 + 属主校验)。
- **传输 fail-closed**:明文 http 默认拒绝;`--insecure` 仅单次旗标(拒绝持久化 + 每次 stderr 告警);`/api/v1/daemon/*` TLS 强制不随 `--insecure` 放宽。
- **暴露面剔除**:公开 OpenAPI 对 `/api/v1/daemon/*` 与内部端点**整体剔除**(非 `x-internal` 标记),CI 零命中断言。
- **覆盖率**:pytest-cov 实测整体与新增代码 ≥90%;每个接口真实 e2e(真服务/真数据库/真浏览器操作)。
- **提交规范**:author/committer = `cnwenf <cnwenf@outlook.com>`;绝无 `Co-Authored-By`;绝不暴露参考来源。

## 阶段拆分与执行记录

### 阶段 A:auth.md 设备码增量(后端,A1–A12)✅

- [x] A1 增量配置(pepper 生产 fail-closed + 轮换宽限窗 + 设备码 TTL/interval)
- [x] A2 `device_authorizations` 表 + sessions 设备/轮换列(状态机 CHECK + 活跃码部分唯一索引)
- [x] A3 码密钥原语(HMAC-SHA256 pepper 仅存哈希;user_code ≥20bit 去歧义字符集;`mesh_rft_` 前缀)
- [x] A4 access JWT 增 `sid`/`workspace_id`/`scope` 声明(会话定位不变量锚点)
- [x] A5 §3.8 有界幂等 refresh 轮换 + 双客户端传输契约(Web cookie / CLI Bearer;宽限窗只发 access、胜者唯一下发)
- [x] A6 统一 Bearer 鉴权依赖(前缀路由 + scopes∩角色 + 代表性端点集成测试)
- [x] A7 Bearer 自省/自撤销端点(GET/DELETE /auth/token;PAT 掩码无明文)
- [x] A8 step-up 链(reauth 端点 + require_recent_auth 会话行查表化 + §1.1 凭证矩阵)
- [x] A9 DeviceCodeService(状态机全分支 + 批准 FOR UPDATE 锁序 + scope 取交 + authenticated_at 快照 + 消费固定锁序 + 爆破防护作废 + reaper)
- [x] A10 设备授权端点 + §3.6 双维度限速(slow_down + Retry-After + 单码违规计数)
- [x] A11 成员移除/停用同事务撤销 cli 会话 + session.revoked 广播 + reaper sweep loop
- [x] A12 设备码真实 e2e(全链路/四轮询分支/真并行单次消费/consume↔移除锁线性化)

### 阶段 B:Web `/device` 确认页 ✅

- [x] 手工录入 user_code(预填仅便利,提交校验录入值防钓鱼)+ scope 人类可读枚举 + 工作区 0/1/多分流(0 禁用/1 自动绑定/多必选无默认)+ i18n 外部化;R4-H1 cookie 契约适配(登录响应无 refresh 明文)

### 阶段 C:CLI 本体(D1–D8)✅

- [x] D1 退码契约(`errors.py` 表驱动,HTTP→exit 数据驱动)
- [x] D2 配置/凭证层(0600 fail-closed/属主/拒符号链接/原子写;flag > env > file > default,`config list --all` 标注来源;alias 单级展开)
- [x] D3 HTTP 客户端(单飞 refresh + 宽限重读、429/5xx 有界重试、verbose 脱敏、传输 fail-closed、自定义 CA 三入口、代理 env-only)
- [x] D4 issue 命令族(list/get/create/update/status/comment/children/dependencies;If-Match 乐观并发;`--web` 深链桥接;`--jq` 内联过滤)
- [x] D5 project/member/agent 命令族
- [x] D6 execution 命令族(get/logs/logs --follow SSE offset 续传去重/cancel;行首 RFC3339 时间戳可关)
- [x] D7 runtime(register 影子记录 + 激活码仅入 0600 sink/status 只读;不触达 daemon 域)
- [x] D8 export/import(流式落盘内存平 + --dry-run + --strict + data-jobs 联动)+ 四 shell 补全

### 阶段 D:OpenAPI 与分发(§5.4)✅

- [x] D9 `docs/api/openapi.yaml`(FastAPI 生成 + daemon 命名空间整体剔除 + 孤儿 schema 按 $ref 可达性回收)
- [x] D10 CI 门禁 `tests/docs/check_openapi_surface.py`(daemon 路径零命中 + CLI 依赖端点齐全);backend-ci 收编 + paths 扩 `docs/api/**`、`cli/**`
- [x] D11 双侧契约测试(backend 应用↔yaml 漂移;cli AST 扫描全部请求构造↔yaml:存在性/路径参数声明/2xx 契约)
- [x] D12 版本协商(`version --verbose` 在线服务端版本;Deprecation/Sunset stderr 告警含 SSE 通道)
- [x] D13 `cli-release.yml` 四平台单二进制 + SHA-256 + minisign(公钥 `cli/mesh-release.pub` 随仓库,runtime.md 同基线);install.sh 校验和 + 签名双验

### 阶段 E:验收修复轮(验收员打回项)✅

- [x] E1/A1 迁移重编 0030(避让主干 0029_p0_contracts,alembic 单 head)
- [x] E2/A2 i18n 内嵌目录 version 按 djb2 重生(与验收实测哈希一致)
- [x] E3/A3 PAT 调 data-jobs 500 修复(`gate_workspace` 令牌分支名册读前置租户上下文)+ PAT×export/import 代表性端点回归
- [x] E4/A4 本归档(writing-plans)
- [x] E5/B1 refresh 轮换文档串与 §3.8/R5-H1 权威口径对齐
- [x] E6/B2 设备授权终态不可逆下沉 DB BEFORE UPDATE 触发器(迁移 0031)+ 契约测试
- [x] E7/C1–C8 LOW 项批量修复(见对应提交)

## 风险与对策(实施中实测命中)

| 风险 | 对策 | 实测 |
|---|---|---|
| 上游主干并行演进(迁移号/i18n 哈希漂移) | 每轮 rebase + alembic heads 单头断言 + 目录哈希重生 | A1/A2 两轮命中并收口 |
| 受限 app 角色 RLS 在单测(owner 角色)不可见 | 关键名册读补 set_tenant_context 不变量 + 角色无关 spy 回归测试 | 自省 500、PAT data-jobs 500 均由真栈 e2e 捕获,spy 测试固化 |
| 流式路径单测盲区 | respx 流式响应 + SSE 帧循环单测(follow 去重/重连/end) | stream_request json 参缺失由此类测试在修复轮暴露 |
