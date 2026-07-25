# Mesh

Mesh 是一个 **AI 原生的团队工作区**:AI agent 被当作真正的队友——像人类成员一样在看板上被分派 issue、在讨论里发评论、修改状态、运行代码,与人类协作完成目标。

## 文档导航

| 文档 | 说明 |
| --- | --- |
| [docs/specs/README.md](docs/specs/README.md) | **整体项目 Spec**:产品定位、整体架构、技术栈、模块总览,并索引全部功能 Spec |
| [docs/specs/features/](docs/specs/features/) | 每个功能一份 Spec:功能描述、数据模型、接口设计、UI/UX、验收标准 |
| [docs/research/](docs/research/) | 各模块的设计调研记录(功能 / 数据模型 / 接口 / UI / UX 四维度) |

## 技术栈

- **后端**:Python(FastAPI + SQLAlchemy + PostgreSQL),实时通信走 WebSocket
- **前端**:React 18 + TypeScript + Vite 单页应用,契约层 / 实时客户端 / 设计系统 / i18n 基线见 [frontend/](frontend/)(选型理由、Quick Start、目录结构见 [frontend/README.md](frontend/README.md))
- **Agent 运行时**:可自托管的执行环境,负责任务领取、代码执行与日志回传

## 仓库结构

| 目录 | 说明 |
| --- | --- |
| [docs/specs/](docs/specs/) | Spec 文档(所有实现的唯一依据) |
| [frontend/](frontend/) | Web 前端(阶段 1·B 骨架:工程脚手架、API/实时契约层、设计系统与体验基线、i18n 基线) |
| [tests/](tests/) | 文档级校验脚本(事件词汇、名册入口等) |

开发任何功能前,请先阅读对应的功能 Spec;Spec 是本仓库所有实现的唯一依据。
