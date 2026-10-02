"""The one refusal type every Evidence Desk check raises."""

from __future__ import annotations


class DeskError(Exception):
  """A deliberate, explainable refusal.

  ``code`` is a stable machine-readable reason. ``message`` is safe to show
  to the agent and owner: it never contains file contents, secrets, or a
  stack trace. Callers fail closed on any ``DeskError``.
  """

  def __init__(self, code: str, message: str, *, status: int = 400):
    super().__init__(message)
    self.code = code
    self.message = message
    self.status = status

  def to_body(self) -> dict:
    """The JSON body a service response returns for this refusal."""
    return {"error": self.code, "detail": self.message}
