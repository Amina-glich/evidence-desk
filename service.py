"""Evidence Desk service entry. The platform runs this file once per request.

All behavior lives in ``desk.service``; this file only has to sit at the app
root, where the platform requires the reviewed service entry to be.
"""

import sys

from desk.service import main


if __name__ == "__main__":
  sys.exit(main())
