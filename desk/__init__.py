"""Evidence Desk service core.

The modules here are imported by the app's reviewed service entrypoint. They
never trust model-written arguments for authority: the project a tool acts on
is derived from the platform's trusted call identity (see ``binding``), and
every project path is opened without following symlinks (see ``project_fs``).
"""

# Version of the on-disk project data formats this code reads and writes.
SCHEMA_VERSION = 1
