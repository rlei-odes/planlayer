"""Planlayer: corporate budgeting and forecasting on Postgres.

Packages:
- ``api``: HTTP routes and request/response models.
- ``domain``: calculation rules (spreading, rounding, signs, periods), pure Python, no I/O.
- ``store``: the app store schema (``planlayer_app``), queries and migrations.
- ``adapters``: reading sources and writing the published output.
"""

from importlib.metadata import version

__version__ = version("planlayer")
