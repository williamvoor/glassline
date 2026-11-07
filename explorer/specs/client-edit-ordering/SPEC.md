# Client edit ordering after failed requests and Board rechecks

Date: 2026-11-07. Status: accepted for implementation of the two findings in
`docs/explorer-refactor-libraries-code-review.md`. Parent: `explorer/SPEC.md` §18
Controls and `specs/workspaces/SPEC.md` N19. Decision: ADR 0050.

## Files

- `client/src/controls.ts`: `BoardControls.changed`, `flush`, `revert`, `restoreDisplayed`.
- `client/src/settings/wizard.ts`: `ExplorerWizard.goTo`.
- `client/test/controls.test.ts` and `client/test/settings/save-lifecycle.test.ts`:
  regression coverage. Existing Board and wizard tests remain the compatibility checks.

## Behavior

A failed Control request is no longer a valid baseline for suppressing a subsequent
send. Preserve a newer unsent slider edit, including when it returns to the failed
request's values; debounce or explicit flush sends that intent again. Success updates
the displayed baseline; failure with no newer intent restores the displayed values.

Entering Boards still rechecks the held Boards against the saved Review. The mounted
Board editor owns this recheck through its validation queue, so an older reply cannot
replace newer text and all saves persist in order. Leaving Boards flushes the newest
text before navigation. Recheck errors are shown in the Board editor and are retryable.

## Criteria → tests

1. Failure while a slider edit is pending, followed by a return to the failed values,
   sends those values again; accepting that request reconciles inputs and displayed
   state → `resends failed Control values when a pending slider returns to them`.
2. Explicit flush after the same failure sends the latest intent and cancels the
   debounce, without duplicate sends → `flushes intent equal to failed Control values`.
3. Returning to accepted or outstanding values does not duplicate a request, and a
   failed retry with no newer intent rolls back → `does not resend accepted or
   outstanding Control values when a slider returns`, plus criterion 1's failure case.
4. Entering Boards from Review, editing while its recheck is delayed, and then
   completing the recheck preserves the visible edit. Its later save persists the
   edited version after the recheck → `preserves edits made during the Board entry
   recheck` (debounce before and after the recheck completes).
5. A failed entry recheck preserves the held text and shows an error; a later edit saves
   and clears it → `preserves edits and permits retry after a Board entry recheck fails`.
6. Navigation during the delayed entry recheck waits for it and then saves the newest
   edit before Export → `flushes edits before Export while a Board entry recheck runs`.

## Decisions and non-goals

Keep sequence-aware Control replies and the current debounce durations. Resend retained
intent after a failure rather than silently canceling it. Keep Board validation in the
editor's existing serial queue rather than adding another wizard-owned revision guard.
External drafts still replace Board text. No new dependencies, settings, server changes,
model calls, changes to redrafting, or unrelated refactors. Existing spec requirements
continue to apply.

## Test command

From `explorer/client`:

```sh
npm test -- test/controls.test.ts test/settings/boards.test.ts test/settings/save-lifecycle.test.ts test/settings/wizard.test.ts
npm run build
```
