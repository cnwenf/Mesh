# mesh CLI

Mesh 开发者平台命令行:与 Web 同源 `/api/v1` 的 REST 瘦客户端。规格权威:[docs/specs/features/cli.md](../docs/specs/features/cli.md)。

## 安装

```bash
pip install ./cli                     # 本地安装(入口 mesh)
# 或经 Releases 的签名二进制:
./install.sh cli-v0.20.0              # 校验 SHA-256 + minisign 签名后安装
```

开发运行:`PYTHONPATH=cli/src python -m meshcli --help`。

## 版本、分发与 OpenAPI(cli.md §5.4)

- **版本协商**:`mesh version` 报告 CLI 版本 + 目标 API 版本;`mesh version --verbose` 追加运行时/平台、配置的 API 基址与**在线服务端 API 版本**(探测公开契约文档)。服务端返回 `Deprecation`/`Sunset` 响应头时,任何命令均在 stderr 打升级提示(含 SSE 流式通道)。
- **公开契约**:[docs/api/openapi.yaml](../docs/api/openapi.yaml)(OpenAPI 3.1,`python backend/scripts/export_openapi.py` 生成)是与本 CLI 对齐的权威 API 契约——`/api/v1/daemon/*` 及内部端点**整体剔除**(非 `x-internal` 标记,孤儿 schema 一并移除)。CI 门禁 `tests/docs/check_openapi_surface.py` 断言 daemon 路径零命中 + CLI 依赖端点齐全;`backend/tests/unit/test_cli_openapi_contract.py`(应用↔yaml 漂移)与 `cli/tests/test_openapi_contract.py`(CLI 请求构造/响应解析↔yaml 漂移)双侧防漂移。
- **签名分发**:推 `cli-v*` tag 触发 `.github/workflows/cli-release.yml`——四平台(linux/darwin × x86_64/aarch64)原生 runner 各构建 PyInstaller 单二进制,每个产物附 **SHA-256 校验和 + minisign 签名**;`install.sh` 先校验和、后验签(公钥 `cli/mesh-release.pub` 随仓库发布,与 runtime.md 安装包同基线),不鼓励盲管道(`curl | sh` 已废)。签名密钥经仓库 secret 注入,绝不进代码库。

## 登录

```bash
# 设备码登录(默认,本地开发):浏览器打开确认页录入 Code 并批准,
# 批准页选定的工作区即 CLI 默认工作区。
mesh auth login

# CI / 无头:PAT 经 stdin(令牌绝不作命令行参数)
echo "$MESH_PAT" | mesh auth login --with-token

mesh auth status            # 凭证信息(prefix 掩码,无明文)
mesh auth logout            # PAT:默认仅清本地;--revoke 服务端吊销
```

## 常用命令

```bash
mesh issue list --workspace acme --all --output json
mesh issue create --title "修复登录" --description-file ./body.md --priority high
mesh issue get ACME-42 --output json --jq '.data.title'
mesh issue comment ACME-42 --content-file ./review.md
mesh project list
mesh member list
mesh agent executions <agent-id> --all
mesh execution logs <id> --follow            # SSE 实时跟随,Ctrl-C 退码 130
mesh execution logs <id> --no-timestamps | grep ERROR
mesh export issues --project <id> -o issues.csv
mesh import issues --file rows.csv --dry-run  # 仅校验,不落库
mesh completion bash >> ~/.bashrc
```

## 契约要点

- **退出码**(稳定契约):`0` 成功 · `1` 通用(5xx/网络/429 重试耗尽)· `2` 鉴权专属(401/403/未登录/过期/吊销)· `3` 校验(400/404/413/415/422 及用法错误)· `4` 冲突(409/423)· `130` 中断。
- **`--output json`**:stdout 恰为单一合法 JSON(成功包络或错误信封);进度/告警/错误一律 stderr。`--jq` 内联过滤 `.data`(内置 jq 子集,不依赖外部 jq)。
- **配置优先级**:`--flag > MESH_* env > ~/.config/mesh/config.yaml > 默认`;`MESH_CONFIG` 改向配置目录,`MESH_TOKEN` 直供 CI 令牌。
- **凭证安全**:`~/.config/mesh/credentials.yaml` 强制 0600(父目录 0700),属主不符 / 符号链接 / 过宽权限一律拒载(退码 2 + 修复指令);原子写(临时文件 0600 → fsync → rename)。
- **传输安全**:默认强制 TLS 校验;`--insecure` 仅单次生效(每次 stderr 告警,绝不持久化);自定义 CA:`--ca-cert` > config `hosts.<host>.tls.ca_cert` > `SSL_CERT_FILE`;代理经 `HTTPS_PROXY/HTTP_PROXY`(带认证代理仅 env,不落配置),`NO_PROXY` 支持后缀/CIDR/`*`。
- **幂等**:写命令 `--idempotency-key`(缺省自动生成)。
- **边界**:`mesh runtime` 仅控制台侧(register 影子记录 + 只读 status);守护进程协议属独立二进制 `mesh-runtime`,本 CLI 不触达 `/api/v1/daemon/*`。

## 开发

```bash
cd cli
uv venv .venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest            # 单测(退码表驱动/配置 fail-closed/jq/SSE 解析)
```

e2e(真服务)在 `backend/tests/e2e/test_cli_e2e.py`(真 uvicorn + 真数据库: PAT/设备码链路、退码契约、JSON 契约)。
