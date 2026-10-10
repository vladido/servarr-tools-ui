import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from .app import Application
from .config import Config


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main():
    host = os.environ.get("LISTEN_HOST", "0.0.0.0")
    port = int(os.environ.get("LISTEN_PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("LISTEN_PORT must be between 1 and 65535")
    app = Application(Config.from_env())
    with make_server(host, port, app, server_class=ThreadingWSGIServer) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
