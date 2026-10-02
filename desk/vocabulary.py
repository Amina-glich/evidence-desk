"""The comparison dimensions and finding statuses every project uses.

These ids appear in agent-written ``evidence/Sn.json`` files, in the
comparison export, and in the agent guidance. Tests keep the guidance and the
starter files in step with this module.
"""

from __future__ import annotations


# (id, label) in the order a comparison presents them.
DIMENSIONS = (
  ("task", "Research task or problem"),
  ("data", "Dataset and data setting"),
  ("validation", "Validation and experimental design"),
  ("results", "Metrics and reported results"),
  ("runtime", "Runtime, deployment, or real-time constraints"),
  ("limitations", "Limitations and gaps"),
)

# (id, label). A dimension with no recorded finding is "not_assessed".
STATUSES = (
  # Supported by at least one page number and exact quotation.
  ("reported", "Reported"),
  # Checked in the available source material and absent.
  ("not_reported", "Not reported"),
  # Not yet checked.
  ("not_assessed", "Not assessed"),
)
