"""Data source adapters. Importing the package registers implementations."""

import adapters.edgar.adapter  # noqa: F401  — registers the 'edgar' adapter
import adapters.moex.adapter  # noqa: F401  — registers the 'moex' adapter
