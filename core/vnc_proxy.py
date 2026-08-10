"""
VNC WebSocket Proxy Middleware (werkzeug 相容版)
將 /novnc-ws 的 WebSocket 連線透明代理到本地 websockify (127.0.0.1:6080)

架構:
  瀏覽器 → HTTPS → Flask/werkzeug → VNCProxyMiddleware → websockify:6080 → x11vnc:5900

策略 (v2 - 修正雙重握手):
  websockify 本身也是 WebSocket 端點，必須由它完成 101 握手。
  middleware 把瀏覽器的 WebSocket Upgrade 請求「原樣轉發」給 websockify，
  再把 websockify 回傳的 101 回應轉回瀏覽器；
  之後僅做純 TCP 位元組流雙向中繼（不解幀、不解析任何協議）。
"""
import socket
import threading

VNC_PROXY_TARGET = ('127.0.0.1', 6080)


def _build_upgrade_request(environ):
    """根據 environ 重建瀏覽器的 WebSocket Upgrade 請求（發送給 websockify）"""
    host = environ.get('HTTP_HOST', '127.0.0.1:6080')
    key = environ.get('HTTP_SEC_WEBSOCKET_KEY', '')
    version = environ.get('HTTP_SEC_WEBSOCKET_VERSION', '13')
    protocol = environ.get('HTTP_SEC_WEBSOCKET_PROTOCOL', '')
    extensions = environ.get('HTTP_SEC_WEBSOCKET_EXTENSIONS', '')
    origin = environ.get('HTTP_ORIGIN', '')

    lines = [
        'GET /websockify HTTP/1.1',
        'Host: %s' % host,
        'Connection: Upgrade',
        'Upgrade: websocket',
    ]
    if key:
        lines.append('Sec-WebSocket-Key: %s' % key)
    lines.append('Sec-WebSocket-Version: %s' % version)
    if protocol:
        lines.append('Sec-WebSocket-Protocol: %s' % protocol)
    if extensions:
        lines.append('Sec-WebSocket-Extensions: %s' % extensions)
    if origin:
        lines.append('Origin: %s' % origin)
    lines.append('')
    lines.append('')
    return '\r\n'.join(lines).encode('latin-1')


def _recv_http_headers(sock, timeout=10):
    """讀取 websockify 的 HTTP 回應頭（直到 \r\n\r\n），可能附帶後續資料（一併返回）"""
    sock.settimeout(timeout)
    data = b''
    try:
        while b'\r\n\r\n' not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    finally:
        sock.settimeout(None)
    return data


def _relay(client_sock, target):
    """雙向位元組流中繼，直到任一端關閉"""
    def pipe(src, dst):
        try:
            while True:
                data = src.recv(8192)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    t1 = threading.Thread(target=pipe, args=(client_sock, target), daemon=True)
    t2 = threading.Thread(target=pipe, args=(target, client_sock), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    try:
        target.close()
    except Exception:
        pass
    try:
        client_sock.close()
    except Exception:
        pass


class VNCProxyMiddleware:
    """WSGI middleware: 攔截 /novnc-ws WebSocket 升級並中繼到 websockify"""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        upgrade = environ.get('HTTP_UPGRADE', '').lower()
        if path == '/novnc-ws' and upgrade == 'websocket':
            client_sock = environ.get('werkzeug.socket')
            if client_sock is None:
                start_response('400 Bad Request', [('Content-Type', 'text/plain')])
                return [b'no client socket (werkzeug.socket missing)']
            target = None
            try:
                # 1) 連到 websockify
                target = socket.create_connection(VNC_PROXY_TARGET, timeout=10)
                # 2) 把瀏覽器的 upgrade 請求轉發給 websockify
                target.sendall(_build_upgrade_request(environ))
                # 3) 讀 websockify 的回應（期望 101 Switching Protocols）
                resp = _recv_http_headers(target)
                if not resp.startswith(b'HTTP/1.1 101'):
                    # websockify 拒絕（例如 400/405）→ 把錯誤回應轉給瀏覽器並結束
                    try:
                        client_sock.sendall(resp or b'HTTP/1.1 502 Bad Gateway\r\n\r\nwebsockify rejected')
                    except Exception:
                        pass
                    try:
                        client_sock.close()
                    except Exception:
                        pass
                    return []
                # 4) 把 websockify 的 101（含可能附帶的 RFB 首包）原樣回給瀏覽器
                client_sock.sendall(resp)
                # 5) 純 TCP 雙向中繼（werkzeug threaded 模式下不影響其他請求）
                _relay(client_sock, target)
                target = None  # _relay 內部已關閉 target
            except Exception as e:
                try:
                    client_sock.sendall(('HTTP/1.1 502 Bad Gateway\r\n\r\nproxy error: %s' % e).encode('latin-1'))
                except Exception:
                    pass
            finally:
                if target is not None:
                    try:
                        target.close()
                    except Exception:
                        pass
            return []
        return self.app(environ, start_response)

    @classmethod
    def wrap_app(cls, app):
        app.wsgi_app = cls(app.wsgi_app)
        return app
