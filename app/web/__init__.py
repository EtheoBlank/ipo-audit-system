"""Web UI — FastAPI + Jinja2 server-rendered HTML.

Why Jinja2 not Next.js
======================
* Vercel 限制: 单 Vercel 项目里 ``api/index.py`` 是 ASGI function, 不能跟
  Next.js App Router 共存 (rewrite 路由会冲突). 改用 FastAPI 自带 Jinja2Templates
  在同一个 Python 函数里出 HTML, 单文件部署.
* 数据流简单: HTML form → POST endpoint → 重定向 / 渲染结果. 不需要
  client-side router / hydration.
* Tailwind CDN 内嵌, 不引入 build 步骤.

为什么不直接用 Streamlit
=======================
* Streamlit 需要持久 WebSocket 连接 (serverless 函数不支持)
* Streamlit 用 HF Space 跑的, 但用户希望 Vercel 完全承担前端角色

页面列表
========
* GET  /            — Dashboard (统计卡片)
* GET  /projects    — 项目列表
* GET  /projects/new — 新建项目表单
* POST /projects/new — 创建项目
* GET  /projects/{id} — 项目详情
* GET  /projects/{id}/import — Excel 上传表单
* POST /projects/{id}/import — Excel 上传处理
* GET  /projects/{id}/trial-balance — 试算平衡
* GET  /knowledge-base — 知识库检索
"""

from .routes import router as web_router  # noqa: F401
