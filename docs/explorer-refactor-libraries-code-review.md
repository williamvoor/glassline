# Review: explorer maintainability fixes and library replacements

**Scope:** implementation commits `9a63b3e` and `485d508`, against parent `f056df4`. The documentation commit `396e3f3` was consulted for context. Findings distinguish a regression in the changed Control handling from a pre-existing race that remains relevant to the stated save-ordering goal.

## Findings

### [P2] Reconcile a failed Control request when a pending slider edit is canceled

**Location:** `explorer/client/src/controls.ts::BoardControls.revert`, `changed` and `flush`

When a request fails while a slider debounce is pending, `revert(seq)` preserves the input as newer intent but leaves `lastSent` set to the failed request's values. If the user then drags the slider back to the value in that failed request, `changed()` sees the full input equal to `lastSent` and cancels the timer. Nothing is sent or rolled back, even though the displayed panels still represent the earlier accepted values.

For example, starting with `hide=false, min=0`: turn `hide` on (request 1), drag `min` to 1, let request 1 fail, then drag `min` back to 0. The input ends at `hide=true, min=0`, but the panels still show `hide=false, min=0`; there is no pending request. This reintroduces the input/panel mismatch that the sequence-aware change is intended to prevent.

Track failed sent state separately from an accepted or still-outstanding request, and reconcile when pending intent is canceled or flushed. Add a regression test covering a failure during a slider debounce followed by a drag back to the failed request's values.

### [P2] A Board recheck can still overwrite an edit before it is saved

**Location:** `explorer/client/src/settings/wizard.ts::ExplorerWizard.goTo` and `explorer/client/src/settings/boards.ts::BsBoards.willUpdate`

On arrival at Boards, `goTo` displays the editor and then awaits an independent `validateBoards(files)` request. If the user edits a Board while this recheck is running, its older response assigns `this.boards` on the wizard. `BsBoards.willUpdate` treats that response as external state and replaces `files`, discarding the unsaved text. Its pending debounce can then save the old text. The new per-editor save queue does not include this wizard-owned recheck.

This race predates these commits; it is not attributed to the refactor. It is nevertheless a remaining gap in the Board edit/persistence lifecycle addressed by the correctness batch. Route the recheck through the same editor-owned ordering mechanism, or guard its response against edits made since it started. Add a deferred-response test that enters Boards, edits before the recheck completes, and verifies that the edit remains visible and is the version eventually saved.

## Fix recheck

Rechecked the subsequent, uncommitted client changes. **Both reproduced scenarios are addressed:** failed Control values no longer suppress the retained slider intent, and the Board arrival recheck now runs through the editor's serial save queue. The added tests cover successful and failed Control retries, both debounce orderings for the Board recheck, recheck failure and retry, and navigation while the recheck is running. No additional actionable findings were identified in this focused recheck.

Verification: `npm test -- --exclude test/interop.test.ts` passed **251 tests**; `npm run build` passed. Existing Settings tests emitted localhost connection-refused errors, and the build retained its large-chunk warning. Interoperability, browser and live-service testing were not run. The findings below remain as the historical review of the original commits, not claims that the working-tree fixes remain broken.

## Verification and limits

- Reviewed the two implementation diffs and relevant Control, Settings, export, Macro, schema and transport code. This was a focused review, not an exhaustive audit.
- Ran two temporary Vitest reproductions against the real client components: one confirmed the failed-Control/debounce mismatch, and one confirmed that a delayed Board recheck replaces an unsaved edit. Both passed; the temporary files were removed. The Board reproduction invoked the same `goTo('boards')` recheck while the Board editor was mounted rather than driving the entire Review-to-Boards flow.
- Did not rerun the full test suites, database integration, build, live model calls or deployment acceptance. Existing test results and the intentional library behavior changes are documented in `docs/explorer-maintainability-review-2026-11-04.md`.
