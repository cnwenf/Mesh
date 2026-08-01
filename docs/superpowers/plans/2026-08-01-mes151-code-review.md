# MES-151 代码审查记录

> 方法：requesting-code-review；基线 `7841b281`，范围为本次合规扫描、CI、Node builder、
> 测试与文档变更。
>
> **2026-08-02 更正**：本记录对“扫描范围完整”的结论已被 alternate-ref 负向复验推翻。
> 原实现的 `git log` 仅遍历 `HEAD` 可达提交，不能覆盖其他 ref 独有提交；原测试也没有建立
> 真实分叉拓扑。修复与新 review 证据见 `2026-08-02-mes151-all-refs-regression.md`。

## 审查清单

| 检查项 | 结论 | 证据 |
|---|---|---|
| 仓库中不保存真实匹配规则 | 通过 | 实现只接受外部文件或 Actions secret，规范与 workflow 均无规则正文 |
| 缺配置时 fail closed | 通过 | 空配置、缺失文件、非法正则、非法 Git root 均有单测；CLI 返回非零 |
| 扫描范围完整 | 原结论撤回 | `git log` 缺少 all-refs revision selection；后续负向复验发现确定性漏检 |
| 日志不二次泄漏 | 通过 | 命中只输出规则编号、来源与行号；单测断言规则和命中正文不出现在 JSON |
| 外部规则误报控制 | 通过 | 对短 token 使用边界约束；修正后全仓扫描零命中，不在仓库记录规则正文 |
| Docker 与项目引擎一致 | 通过 | 测试从 `package.json` 解析下限并校验 Docker 精确 patch；镜像实测 Node v22.22.0 |
| 中间件暴露与凭据 | 通过 | Compose 使用独立强随机凭据；三项中间件均无宿主端口；资源已清理 |
| 测试充分性 | 通过 | 新模块 12 项单测、branch coverage 100%；前端全套及 Spec checks 通过 |
| 文档一致性 | 通过 | 根 README、frontend README 与 clean-room Spec 同步说明运行时和门禁 |

## 发现与处置

1. **已修复（高）**：最初外置短词规则会命中无关标准 API 标识。逐条定位 11 个命中，
   将该规则在仓库外加 token 边界后重扫为零；敏感正文未写入日志或仓库。
2. **已修复（中）**：首次 targeted coverage 四舍五入显示 90%，原始值低于严格门槛。
   增补异常与文件边界测试后，97 statements / 28 branches 均 100%。
3. **已验证（中）**：Compose 原配置为对象存储暴露开发端口。验收复跑使用临时 override
   清空映射，并确认 PostgreSQL、Redis、MinIO 的 `docker port` 均为空；override 未纳入提交。
4. **后续发现（高）**：alternate ref 独有提交不在原 `git log` 的 revision set 中；本记录
   当时使用的测试让 ref 指向当前提交，没有覆盖该拓扑，因此“无未解决阻断项”结论撤回。

## Review 结论

本记录的原通过结论已失效，不再作为放行依据。后续修复必须以真实 alternate-ref 回归、
source-provenance 与 backend 完整 CI，以及新的 review 结论为准。
