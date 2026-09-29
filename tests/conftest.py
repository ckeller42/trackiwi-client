import http.client
import io
import urllib.request
import urllib.response


class FakeResponse(io.BytesIO):
    """Stand-in for an HTTP response returned by urlopen."""

    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        # Real responses carry a case-insensitive `HTTPMessage`, not a dict.
        self.headers = http.client.HTTPMessage()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeOpener:
    """Stand-in for urllib.request.urlopen that records the requests it got."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Response(urllib.response.addinfourl):
    """`addinfourl` plus the `msg` reason phrase `HTTPErrorProcessor` reads."""

    def __init__(self, body, headers, url, code):
        super().__init__(io.BytesIO(body), headers, url, code)
        self.msg = "Found" if code == 302 else "OK"


class RedirectingHTTP(urllib.request.BaseHandler):
    """Offline stand-in for a proxy: 302 to a login page, which then answers 200.

    `handler_order` below the stock `HTTPHandler` (500) makes the opener ask
    this handler first, so no socket is ever opened. It records what reached
    it, which is exactly what a real server would have seen.
    """

    handler_order = 100

    def __init__(self):
        self.seen = []

    def http_open(self, req):
        self.seen.append((req.get_method(), req.full_url, req.get_header("Authorization")))
        headers = http.client.HTTPMessage()
        code = 200
        if len(self.seen) == 1:
            headers["Location"] = "http://sso.example.invalid/login"
            code = 302
        return _Response(b"<html/>", headers, req.full_url, code)

    https_open = http_open
