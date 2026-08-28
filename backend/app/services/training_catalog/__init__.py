"""Training-catalog services.

The dynamic catalog expansion and publication subsystem was deleted: every
goal template and training context in every environment was `SEEDED`, and
the model never generated one. Catalog reads live in
`app.repositories.training_catalog`. This package is kept as a namespace for
pure, catalog-related services (for example goal grouping) without an
expansion write path.
"""

from __future__ import annotations

__all__: list[str] = []
