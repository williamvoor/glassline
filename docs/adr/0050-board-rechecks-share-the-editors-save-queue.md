# Board rechecks share the editor's save queue

On entering Boards, a wizard-owned recheck could replace edits made while its request
was running and later persist the older text. Route that recheck through the mounted
Board editor's existing serial validation queue, which already protects newer edits
and orders persistence. This keeps one owner for Board saves instead of maintaining a
second revision guard in the wizard. Failed Control requests likewise cannot suppress
a later send of retained user intent. See `explorer/specs/client-edit-ordering/SPEC.md`
for the 2026-11-07 regression contract.
