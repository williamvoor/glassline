---
status: accepted (2026-11-05, the user)
---

# Datasets are deployment data, not part of the explorer

The explorer must not look tied to any dataset. Someone outside Australia has no use for the
AusTender files, and will always run the bootstrap (the New Workspace wizard in Workspace mode)
to describe their own database. So Datasets stop living in `explorer/`:

- **`explorer/datasets/austender` and `explorer/datasets/vicprop` move to `datasets/austender`
  and `datasets/vicprop` at the repo root.** They stay committed, as the owner's own deployment,
  including the reviewed Wonder-drafted Boards (ADR 0036). `explorer/` ships no Dataset.
- **The bootstrap CLI's default `--root` is `~/.glassline`**, outside the repo; `export` creates
  it when missing. An explicit `--root` must exist. `explorer/datasets/` stays git-ignored so old
  local copies aren't committed.
- **Tests use a frozen copy** at `explorer/agent/tests/fixtures/austender/`. It matches the
  AusTender test seed (ADR 0010) and is independent of the deployment copy.
- **`DATASET_DIR` (single-dataset mode) accepts absolute and `~` paths.** A relative path is still
  relative to `explorer/`, so the owner writes `../datasets/austender`.
- **Workspace mode (`EXPLORER_DATA_DIR`, outside the repo) is the recommended setup** in
  `.env.example`. Single-dataset mode stays for tests and `run.sh`.

The dataset-agnostic rule is unchanged: no AusTender names in code, prompts or shared test
fixtures. The frozen fixture is test data and says so.

This supersedes one line of ADR 0041, "Repo Datasets such as `explorer/datasets/austender` are
listed read-only and change only through a commit". There are no repo Datasets under `explorer/`
now. The owner's committed Datasets under `datasets/` are not listed by Settings unless a
Workspace points at them. They change through a commit or a bootstrap export. The rest of ADR
0041 holds: Settings writes only into `EXPLORER_DATA_DIR`.

## Considered Options

- **Keep the Datasets in `explorer/`.** Nothing to move, but a fresh clone looks like an
  AusTender explorer, and users elsewhere carry files they will never use.
- **Delete them from the repo and regenerate.** Cleanest tree, but a Dataset holds reviewed
  Boards, notes and scenarios that cost paid Wonder runs and human review. Regenerating loses
  that review.
- **Point the tests at the deployment copy.** No duplicate, but a re-bootstrap or an owner edit
  to notes or Boards would break tests. Rejected. The frozen fixture trades a copy for stability.
