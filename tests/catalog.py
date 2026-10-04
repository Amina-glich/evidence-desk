"""Offline arXiv responses for tests: the recorded fixture and synthetic feeds.

No test may reach the network. ``FakeTransport`` stands in for
``arxiv.transport``: it answers from canned responses and fails the test on
any request it does not expect.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import urllib.parse
from pathlib import Path

from tests.support import REPO_ROOT


FIXTURE = (REPO_ROOT / "tests" / "fixtures" / "arxiv" / "1706.03762.atom.xml").read_bytes()
FIXTURE_SHA256 = "5bba183d41a014e2768ebf24c5e161a87ee2cff6d7ffa078fdde20a97ca64a0b"
ATOM = "application/atom+xml; charset=utf-8"


def entry_xml(arxiv_id: str, title: str, *, authors=("A. Author",), summary="A synthetic abstract.",
              extra: str = "", entry_id: str | None = None) -> str:
  names = "".join(f"<author><name>{name}</name></author>" for name in authors)
  return (
    f"<entry><id>{entry_id or 'http://arxiv.org/abs/' + arxiv_id}</id><title>{title}</title>"
    "<updated>2020-01-02T03:04:05Z</updated><published>2020-01-01T00:00:00Z</published>"
    f"<summary>{summary}</summary>{names}"
    '<arxiv:primary_category term="cs.LG"/><category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>'
    f"{extra}</entry>"
  )


def feed(*entries: str, total: int | None = None) -> bytes:
  return (
    "<?xml version='1.0' encoding='UTF-8'?>"
    '<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
    'xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">'
    "<id>https://arxiv.org/api/test</id><title>arXiv Query</title>"
    f"<opensearch:totalResults>{len(entries) if total is None else total}</opensearch:totalResults>"
    + "".join(entries) + "</feed>"
  ).encode("utf-8")


SEARCH_FEED = feed(
  entry_xml("1705.03122v3", "Convolutional Sequence to Sequence Learning", authors=("J. Gehring", "M. Auli")),
  entry_xml("1706.03762v7", "Attention Is All You Need", authors=("A. Vaswani",)),
  total=2,
)


class FakeTransport:
  """Answers by request parameters; records every call."""

  def __init__(self, responses=None, *, default=None):
    # {"id_list=1706.03762": (status, content type, body, headers)} or a callable.
    self.responses = responses or {}
    self.default = default
    self.calls: list[tuple[str, dict, float]] = []

  def __call__(self, url, headers, timeout):
    self.calls.append((url, dict(headers), timeout))
    query = urllib.parse.urlsplit(url).query
    params = dict(urllib.parse.parse_qsl(query))
    for key, answer in self.responses.items():
      name, _, value = key.partition("=")
      if params.get(name) == value:
        return answer(url) if callable(answer) else answer
    if self.default is not None:
      return self.default(url) if callable(self.default) else self.default
    raise AssertionError(f"unexpected arXiv request: {url}")


def no_network(url, headers, timeout):
  raise AssertionError(f"a test tried to reach the network: {url}")


@contextlib.contextmanager
def temporary_state_lock():
  """Replaces ``project_fs.app_lock`` where the real lock is unavailable (Windows)."""
  handle = tempfile.NamedTemporaryFile(delete=False)
  handle.close()
  fd = os.open(handle.name, os.O_RDWR)
  try:
    def fake_lock(_storage, _name, *, timeout):
      return contextlib.nullcontext(fd)
    yield fake_lock
  finally:
    os.close(fd)
    Path(handle.name).unlink()
