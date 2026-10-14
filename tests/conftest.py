import json

import pytest
import requests

from campus_recruitment.client import RecruitmentClient


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedSession(requests.Session):
    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
        self.sent = []
        self.settings = []

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        self.settings.append((url, proxies, stream, verify, cert))
        return {"proxies": {"https": "http://configured-proxy:8080"}, "stream": False, "verify": verify, "cert": cert}

    def send(self, prepared, **kwargs):
        self.sent.append((prepared, kwargs))
        if not self.outcomes:
            raise AssertionError("Unexpected extra request")
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        result.request = prepared
        result.url = prepared.url
        return result


def response(payload=None, *, status=200, headers=None, body=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update({"Content-Type": "application/json", **(headers or {})})
    result._content = body if body is not None else json.dumps(payload).encode()
    result._content_consumed = True
    return result


@pytest.fixture
def make_client():
    def factory(outcomes, **options):
        clock = FakeClock()
        events = []
        session = ScriptedSession(outcomes)
        client = RecruitmentClient(session=session, sleep=clock.sleep,
                                   monotonic=clock.monotonic, emit=events.append, **options)
        return client, session, clock, events
    return factory
