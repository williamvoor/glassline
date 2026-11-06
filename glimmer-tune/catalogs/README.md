# Vendored catalogs

Both come from [a2ui-project/a2ui](https://github.com/a2ui-project/a2ui) at commit
`825beac2813a6d0ccef3b6fb4124ad6c3c8419f0`.

- `basic_v0_9.json`: the A2UI v0.9 basic catalog, used for training, eval and the
  review page. Copied from `specification/v0_9_1/catalogs/basic/catalog.json`
  (itself byte-identical to `specification/v0_9/...`) with **one line inserted** after
  `catalogId`: `"protocolVersion": "0.9",`, which the SDK's catalog loader requires.
  Every other byte is upstream's; `tests/test_v0_9.py::test_vendored_catalog_matches_upstream`
  checks that.
- `basic_v1.json`: the A2UI v1.0 basic catalog, copied unmodified from
  `catalogs/basic/v1/catalog.json`. The gallery seeds are v1, so the tests use it to check
  the seed conversion and to keep the scorer working on both versions.

Copyright 2024 Google LLC. Licensed under the Apache License, Version 2.0
(https://www.apache.org/licenses/LICENSE-2.0).

Keep these files in step with the SDK pin in `pyproject.toml`.
