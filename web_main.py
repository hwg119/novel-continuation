# -*- coding: utf-8 -*-
"""启动本机 Web 工作台：python web_main.py"""
from pathlib import Path
import threading

import uvicorn

from core.config_manager import APP_ROOT, apply_proxy, get_workspace_root, load_config
from web.api import create_app


class WorkbenchServer(uvicorn.Server):
    """Close long-lived chat streams before Uvicorn waits for connections."""
    def __init__(self, config, app):
        super().__init__(config)
        self.shutdown_requested = threading.Event()
        app.state.shutdown_requested = self.shutdown_requested

    async def shutdown(self, sockets=None):
        self.shutdown_requested.set()
        await super().shutdown(sockets=sockets)


def main():
    config = load_config(str(Path(APP_ROOT) / "config.json"))
    apply_proxy(config)
    workspace = get_workspace_root(config)
    frontend = Path(APP_ROOT) / "frontend" / "dist"
    app = create_app(workspace, str(frontend))
    print("本机工作台：http://127.0.0.1:8765")
    if not (frontend / "index.html").is_file():
        print("尚未构建 Vue 前端。先在 frontend 执行 npm install 和 npm run build。")
    server = WorkbenchServer(uvicorn.Config(app, host="127.0.0.1", port=8765,
                                          timeout_graceful_shutdown=5), app)
    server.run()


if __name__ == "__main__":
    main()
