[@Mesh 验收员](mention://agent/50c3bdd4-625e-47b5-b7c1-b1995b4147a5) 第二/三轮打回项已全量整改,PR #44(https://github.com/cnwenf/Mesh/pull/44)已 rebase 最新 main(kanban 0.13.0 之上,迁移链 0013→0014→0015 单 head,全新库实测),force-push 完成。逐条映射如下,请第三轮复核。

## 后端复核 F1–F8

- **F1 CRITICAL 隔离区 sweep 抢跑**:
  - `backend/src/mesh/attachment/processing.py::claim_pending_blobs` —— 领取集追加 EXISTS 过滤:仅领取「存在存活 completed 引用附件」的 blob,在途上传(对象未传完)不再被扫成 OBJECT_MISSING。
  - `backend/src/mesh/attachment/service.py::complete_upload`/`complete_multipart` —— 按 §3.3 第 3 步显式置 `blob.scan_status='pending'` 并清除陈旧终态(error_code/attempts),自愈任何遗留 error;`scan_requested` outbox 键按次生成以重触发 relay。
  - 回归:`test_sweep_skips_blobs_without_completed_references`(sweep tick 跨在途上传仍可下载)、`test_complete_resets_errored_blob_for_rescan`(error blob 经 complete 复位重扫放行)。
- **F2 HIGH ref_count 永久泄漏**:complete 两条失败路径(对象缺失 / 尺寸不符)同事务 `_ref_count(-1)` + 删残留对象(对齐 abort/过期)。回归:`test_complete_size_mismatch_releases_ref_count_and_object`、`test_complete_head_size_mismatch_releases_ref_count`、`test_failed_upload_retention_then_gc_reclaims_blob_row`(failed→retention→GC 全链 blob 归零回收)。
- **F3 HIGH THUMBNAIL_FAILED 闸门外扩**:缩略图失败降级为非终态告警——`scan_status` 保持 `clean`,`scan_detail.thumbnail_error` 记录,原始文件保持可下载,前端回落文件卡片 / thumbnail 端点 404 not ready。回归:`test_thumbnail_failure_keeps_clean_and_downloadable`。
- **F4 MEDIUM 签名尺寸绑定**:`storage.py::presign_put` 增 `content_length` 入签(S3v4 绑定 Content-Length,MinIO 实测拒收尺寸不符 SignatureDoesNotMatch)+ `ensure_bucket` 挂 `AbortIncompleteMultipartUpload`(1 天)生命周期兜底。回归:`test_oversized_put_rejected_by_signature_binding_then_complete_fails`、`test_multipart_lifecycle`。
- **F5 LOW AV 死代码分支**:可执行魔数(MZ/ELF/Mach-O)或嗅探为可执行 MIME 一律 infected(白名单在 request 阶段即拒可执行,抵达扫描器即可疑)。回归:`test_executable_magic_is_always_infected` + e2e EICAR 永久拒。另修 `head_size` 对无 `.response` 异常(连接失败)的健壮性。
- **F6 LOW 幂等键跨成员重放**:迁移 0015 唯一索引改 `(workspace_id, uploader_id, idempotency_key)`;重放查询按上传者过滤;并发同键冲突捕获后回放首条(不再裸 500)。回归:`test_idempotency_key_is_scoped_per_uploader`。
- **F7 LOW 无配额行并发前置校验**:无 `attachment_quotas` 行时取工作区级事务咨询锁串行化。回归:`test_quota_default_path_serializes_concurrent_requests`(并发恰一 423)。
- **F8 覆盖率**:worker loop 重构出 `run_scan_pass`/`run_maintenance_pass` 单次通道,loop wrapper 确定性覆盖。attachment 模块覆盖 92%(routes 91%),整体 **94.95%**(门禁 90%)。

## 前端 12 项(复核 H1/M1–M6/L1–L3/LOW)

- **H1 CRITICAL 秒传死循环**:`useAttachmentUploader.ts` `upload===null` 时跳过 `/complete`,直接由 upload-request 响应合成附件(按 `scan_status` 落 scanning/ready);矛盾单测改写为后端真实形状(断言零 `/complete` 调用)+ 409 再-complete 双层回归(hook 层失败条目不卡死提交、composer 层重试→秒传→可提交)。浏览器走查 spec 增 composer 秒传段(真浏览器复打同内容→第二张缩略图入网格,无 409)。
- **M1 预中止 XHR 悬挂**:signal 已 abort 时立即以 AbortError reject(不构造 XHR);settle 后移除 abort 监听;三处 MockXHR 改真实浏览器语义(未 send 调 abort 不触发事件)。
- **M2 卸载无清理**:卸载清理中止全部在途控制器 + 对已得 attachmentId 的未完成条目尽力服务端 abort,无卸载后 setState。
- **M3 感染文件不可删**:删除动作恒渲染(网格 + 文件卡),下载/复制仍门控于放行态(服务端 403 兜底)。
- **M4 null payload 崩溃**:`realtime.ts` 两个合并函数对 null/非对象 payload 原样返回 + 用例。
- **M5**:上一轮已修(uploadsRef 逐渲染镜像,cancel 同步读),保持。
- **M6 缩略图永久占位**:抽出 `components/Thumbnail.tsx`——初次解析失败重试一次;`<img onError>` 重取新鲜签名 URL(封顶 2 次),重取期间回落占位。
- **L1 copyLink**:改复制稳定鉴权端点 `download_url`(点击时重新鉴权 + 重过扫描闸门),不再复制 60s 签名 URL。
- **L2 triggerDownload**:协议白名单(仅 http/https),拒 javascript:/data:。
- **L3 灯箱竞态**:`lightboxIdRef` 记录当前灯箱附件 id,慢响应归属校验,过期响应丢弃。
- **LOW 测试补丁泄漏**:`Blob.prototype.arrayBuffer` 补丁改逐个安装/拆卸(afterEach 恢复)。
- **§4.3 灯箱**:缩放(0.5×–4×)/ 旋转(90° 步进)/ 重置 / 在附件区定位(关灯箱滚动定位到条目)。
- **§4.4 agent 产出物**:头像占位(显示名首字符)+「来自 <agent> 运行」来源标记(后端 uploader 显示名解析:人类 JOIN users;agent 于 agents 表就位后按 `(workspace_id, agent_id)` 取名,表未就位留空)。
- **IssueDetailPage 防御**:`children_progress` 防御性收窄(`?? 0`),单条坏响应不崩整页 + 回归用例。

## CI 修复

- **backend-ci MinIO**:GitHub services 无法给 minio/minio 传 `server /data`(默认 entrypoint 无参即退出),改 job 步骤 `docker run` + 健康等待。
- **frontend quality**:IssueDetailPage fetch 桩改 URL 感知(附件列表请求恒定空页、不消耗顺序队列,消除 CI 间歇红);存证去重(保留单套最新走查存证);playwright.config 真实 spec 以 `real-*.spec.ts` glob 排除。
- **attachment-e2e job**:services postgres/redis + docker run MinIO + lockfile 安装后端 + 迁移 + 起 api/gateway/worker + Playwright 真浏览器走查;step-level `working-directory` 相对 GITHUB_WORKSPACE 修正。
- **e2e 死锁定位修复**:CI/本地曾现 DeadlockDetected 级联——两层根因:(a) 我方单测后台 loop 任务与逐测试 TRUNCATE 竞争 → 重构为同步单次通道消除;(b) e2e worker 夹具原为 session 作用域,relay 存活至无关 e2e 文件,其 outbox 行锁与 TRUNCATE 的 realtime_events 锁成环 → 改 module 作用域(relay 仅存活于附件 e2e 文件期间)。修复后全量 e2e 162 例本地全绿,无复现。

## 最终树实测(非旧结果顶账)

- 后端:全量单测 + e2e **162 例全绿**,`pytest-cov` 整体 **94.95%**(门禁 90%),ruff 全绿,pip-audit --strict 双 lockfile 零 CVE。
- 前端:typecheck/lint 0 错,vitest 135 文件 **1366 例全绿**,IssueDetailPage **10 连跑全过**,mock e2e 30/30。
- 真实浏览器走查(CI attachment-e2e job 与本机双绿):注册/登录→composer 直传 MinIO(进度卡)→扫描中占位(T14 UI 态)→放行后缩略图实时出现→灯箱缩放/旋转/定位/逐字节下载→composer 秒传(H1 回归)→文件卡→删除。
- 迁移链 0001→0015 单 head 全新库实测;事件词汇/名册守卫/文档校验全绿。
