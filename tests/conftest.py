import io


class FakeResponse(io.BytesIO):
    """Stand-in for an HTTP response returned by urlopen."""

    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

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
