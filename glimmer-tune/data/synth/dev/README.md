# Synthetic dev: regression guard only

200 frozen rows from the run 2 skeleton generator, which is now in `archive/`. Under
Approach v2 (SPEC "Approach v2") they are a **regression guard**, not a selection metric:
gallery dev (`data/dev`) is the primary score.

They contain vocabulary written by `meta/muse-spark-1.3` (ADR 0009). That is allowed for
evaluation only; these rows must never become training data.
