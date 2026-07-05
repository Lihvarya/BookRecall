# BookRecall Web Frontend

这是 BookRecall 的新版 Web 前端工程，使用 Vue 3 + Vite + TypeScript + Tailwind CSS + Pinia。

## 开发方式

先启动 Python 后端：

```powershell
cd D:\BookRecall
.\.venv\Scripts\python.exe bookrecall.py serve --host 127.0.0.1 --port 8000
```

再启动前端开发服务器：

```powershell
cd D:\BookRecall\frontend
npm install
npm run dev
```

Vite 会把 `/api` 和 `/health` 代理到 `http://127.0.0.1:8000`。

## 构建并交给 Python 后端服务

```powershell
cd D:\BookRecall\frontend
npm run build
```

构建后会生成 `frontend/dist`。BookRecall 的 Python Web 服务会优先读取 `frontend/dist/index.html` 和 `frontend/dist/assets/*`；如果没有构建产物，则自动回退到旧版 `src/bookrecall/web_assets`。

## 为什么这样拆

- Vue/Pinia 负责复杂状态：书籍、会话、Agent 配置、trace、导入与索引操作。
- Vite 负责本地开发和生产构建，避免浏览器运行时依赖 CDN。
- Tailwind 负责现代 UI 的一致性，同时保留少量全局 CSS 处理阅读器、高亮和滚动条。
- Python 后端继续作为 API 和静态文件服务层，不引入 Node 运行时到正式服务流程。
