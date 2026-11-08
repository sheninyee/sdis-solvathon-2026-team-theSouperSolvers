import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import data

HOST = "localhost"
PORT = 8001  # change by computer # -----------------------------------------
STATIC = Path(__file__).with_name("static")


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


class WebsiteHandler(BaseHTTPRequestHandler):
    def _send(self, body, ctype, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _filters(self, query):
        return dict(
            year=query.get("year", ["all"])[0],
            opp_type=query.get("type", ["all"])[0],
            agencies=query.get("agency", []),
        )

    def _file(self, path):
        path = path.resolve()
        if STATIC.resolve() not in path.parents or not path.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(path.read_bytes(), ctype)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/":
            self._file(STATIC / "index.html")
        elif url.path.startswith("/static/"):
            self._file(STATIC / url.path[len("/static/"):])
        elif url.path == "/api/meta":
            self._send(json.dumps(data.meta(), default=_json_default), "application/json")
        elif url.path == "/api/data":
            body = json.dumps(data.payload(**self._filters(query)), default=_json_default)
            self._send(body, "application/json")
        elif url.path == "/download":
            self._send(data.download_csv(**self._filters(query)), "text/csv; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="souper_grants_summary.csv"'})
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), WebsiteHandler)
    print(f"Souper Grants is running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()