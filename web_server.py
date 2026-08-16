# -*- coding: utf-8 -*-
"""对话记录前端服务器: http://127.0.0.1:8899（后台常驻）"""
import http.server, json, os, socketserver

BASE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(BASE, "history.jsonl")
WEB_DIR = os.path.join(BASE, "web")
PORT = 8899

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def do_GET(self):
        if self.path == "/history.json":
            entries = []
            if os.path.exists(HISTORY):
                with open(HISTORY, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except Exception:
                                pass
            data = json.dumps(entries, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return super().do_GET()

    def log_message(self, *a):
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
    print(f"对话记录前端: http://127.0.0.1:{PORT}")
    httpd.serve_forever()
