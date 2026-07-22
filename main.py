#!/usr/bin/env python
"""Root entry point for CMR Renamer.

PyInstaller builds the executable from this file using absolute imports,
which avoids the "relative import with no known parent package" error
that occurs when building from within the cmr_renamer package.
"""

from cmr_renamer.watcher import run
import sys

sys.exit(run())