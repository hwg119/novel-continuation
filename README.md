# 小说续写工作台（novel-continuation）

面向已有小说的本机续写工具。工作台采用 Vue + FastAPI，复用 Python 续写、检索和审校逻辑。一本书对应一个工程目录，正文和设定均保存在本机。

## 启动 Web 工作台

需要 Python 和 Node.js。首次运行：

```powershell
cd novel-continuation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm install --prefix frontend
npm run build --prefix frontend
python web_main.py
```

打开 <http://127.0.0.1:8765>。服务仅绑定 `127.0.0.1`。修改 Python 后重启 `web_main.py`；修改前端后重新运行 `npm run build --prefix frontend` 并刷新浏览器。开发前端时可运行 `npm run dev --prefix frontend`，Vite 会把 `/api` 请求转发到 Python 服务。

已有工程可以直接导入，不需要转换工程文件。

## Web 页面与流程

- **首页**：汇总工程状态、最近章节、Wiki 待办和运行记录，并提供下一章续写入口。
- **小说正文**：按章节浏览、阅读和编辑正文，查看插图与摘要，搜索全书内容，比较修订稿并导出 Word。
- **续写与分幕**：维护全书规则、当前章规划和分幕大纲；支持模型提取、按问题修改以及续写生成。
- **审校与修订**：使用模型检查人物、设定和时间线一致性，并生成可比较、可编辑、可回看的整章修订建议。
- **小说 Wiki**：从原文增量整理人物、事物、经历、状态和关系证据，为续写与审校提供可追溯参考。
- **人物关系图**：以人物和原文关系记录构建交互图谱，可按人物与章节范围查看关系变化。
- **工程与母本**：新建或导入工程、导入母本、管理章节范围和向量库。
- **运行记录**：按时间查看后台任务、生成过程、模型回复和错误信息；底部控制台同步显示当前任务进度。
- **全局配置**：管理并排序大模型配置，选择默认续写模型和当前向量模型，以及维护代理、工作目录等跨工程设置。

各页面有独立 URL，章节相关地址包含工程 ID 和章号；刷新或使用浏览器前进、后退不会默认跳回首页。

## 插图与 Word 导出

每章可以根据正文和章节规划生成独立的 SVG 插图，并在页面中预览或重新生成。插图会随当前章一起导出到 Word；没有插图时仅导出标题和正文。Word 使用适合中文阅读的排版，并包含章号和页码。

插图生成需要可用的大模型配置，Word 导出本身不会调用模型。

## 工程数据与持久化

```text
workspace/
  projects.json                    工程索引
  <小说工程>/
    project.json                   本书设定与分章规划
    chapters/chapter_N.txt         母本与续写章节
    vectorstore/                   FAISS 向量索引及 SQLite 元数据
    wiki/chapters/chapter_N.json   按章提取的 Wiki 事实与正文哈希
    illustrations/chapter_N.svg    已生成的本章插图（可选）
    exports/chapter_N.docx         Word 导出文件
    runs/web_jobs/                 Web 后台任务状态和日志
    runs/revisions/                章节旧稿快照
```

`config.json` 保存全局模型等配置，包含密钥；`workspace/` 保存用户小说。这两者已加入 `.gitignore`。章节保存会检查文件版本，避免覆盖另一浏览器窗口中的修改；实际保存时会留下旧稿快照。耗时任务在后台运行，服务重启后未完成的任务标记为“已中断”。

## 可选的本地模型

- **Ollama 大模型**：在“全局配置 → 大模型配置”选择 `Ollama`，填写 Base URL（例如 `http://localhost:11434/v1`）和模型名。续写调用 Ollama 原生 `/api/chat`。
- **本地 BGE-M3 Embedding**：运行 `python tools/embedding/dl_bge_m3.py` 下载模型，再运行 `python tools/embedding/embedding_server.py`。向量配置可指向默认的 `http://127.0.0.1:11435`。没有模型时可用“离线哈希”验证流程，但检索质量较低。

## 验证

```powershell
python -m pytest -q tests
npm run build --prefix frontend
npm run test:diff --prefix frontend
npm run test:routes --prefix frontend
```

本项目的 LLM / Embedding 适配、分幕续写和一致性检查思路，参考并复用了 AI_NovelGenerator（AGPL-3.0）的实现。
