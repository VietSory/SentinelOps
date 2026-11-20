"""Fixed, bounded 128Mi allocation: OOM at 64Mi, healthy at 256Mi.

Use only in the explicitly labelled demo Deployment. There is no fault endpoint,
unbounded allocator or application/database dependency.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def main():
    allocation = bytearray(128 * 1024 * 1024)
    # Touch every page so Linux must account the physical memory to this cgroup.
    for index in range(0, len(allocation), 4096):
        allocation[index] = 1

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            body = json.dumps({'ok': True, 'allocation_mib': 128}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()


if __name__ == '__main__':
    main()
