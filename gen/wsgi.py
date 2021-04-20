import os

from .app import create_app


if 'WERKZEUG_SERVER_FD' in os.environ:
    import socket

    sock = socket.socket(fileno=int(os.environ['WERKZEUG_SERVER_FD']))
    try:
        hostname, port = sock.getsockname()
    finally:
        sock.detach()
    project_url = f"http://{hostname}:{port}"
else:
    print('no', os.environ.get('WERKZEUG_SERVER_FD', ''))
    project_url = ''

app = create_app(os.environ['GEN_PROJECT_ROOT'], project_url=project_url)
