# MES-151 代码审查记录

> 方法：requesting-code-review；基线 `7841b281`，范围为本次合规扫描、CI、Node builder、
> 测试与文档变更。

## 审查清单

| 检查项 | 结论 | 证据 |
|---|---|---|
| 仓库中不保存真实匹配规则 | 通过 | 实现只接受外部文件或 Actions secret，规范与 workflow 均无规则正文 |
| 缺配置时 fail closed | 通过 | 空配置、缺失文件、非法正则、非法 Git root 均有单测；CLI 返回非零 |
| 扫描范围完整 | 通过 | 当前全部受管文本、完整 commit message/author 与 refs 均进入扫描；CI 完整检出历史 |
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
4. **无未解决阻断项**：当前 diff 未发现秘密、规则正文、弱凭据、运行时版本漂移或未覆盖的新逻辑。

## Review 结论

代码层审查通过，可进入 PR CI。合并条件为 source-provenance、backend、frontend、Spec
相关 checks 全绿；合并后需核对远端 `main` 再提交复验。
