# SPEC: Text on the Surface, and blocked questions

Follow-ups from the effort check runs (`docs/followups-2026-11-02.md`). Design choice in ADR 0013.
Sub-spec of `explorer/SPEC.md`; ADR 0001 still holds (bare numbers are logged, not rejected).

## Files

Paths relative to `explorer/agent/` unless stated.

| File | Change |
|---|---|
| `src/explorer_agent/ui.py` | fill placeholders in model-written `Text` before macro expansion (`UiPipeline.build`, ~line 141); scan it for bare numbers; one combined log line (~line 157) |
| `src/explorer_agent/macros.py` | `fill_surface_text` next to `fill_prose` (~line 462); fill `title` in `_chart` (~line 281) and `DataTable` (~line 377), `label` in `Metric` (~line 402) |
| `src/explorer_agent/llm.py` | `CONTENT_POLICY_PROSE` next to `MODEL_FAILED_PROSE` (line 31); chosen in the `_ModelFailed` handler (lines 207-212) |
| `scripts/effort_check.py` | `classify_failure` (line 95) gains `content_policy`; `_BARE` (line 233) follows the new log wording; `surface_summary` (line 186) finds metrics by `Card` |
| `tests/test_ui.py`, `tests/test_llm.py`, `tests/test_effort_check.py` | tests below |
| `docs/followups-2026-11-02.md` (repo root) | #1 and #2 rewritten with the real diagnosis |
| `docs/reasoning-effort-check.md` (repo root) | one line: `metrics` in runs before this fix over-report |

## Behaviour

1. **Model-written `Text` on the Surface.** Every `Text` component the model writes directly (not
   one a Macro builds) has its `text` filled like prose: `{rN[i].col|fmt}` placeholders become
   formatted, markdown-escaped values from stored Results. A placeholder that fails to fill adds a
   UI error naming the component ID (`Text 't': ...`), so the reply goes to a Repair. Numbers the
   model typed in that text are bare numbers, reported alongside the prose's.
2. **Macro titles and labels.** `BarChart`, `LineChart` and `DataTable` `title` and `Metric`
   `label` are filled the same way. A failing placeholder is a macro error (`Metric: ...`).
   These arguments are not scanned for bare numbers (out of scope, see Decisions).
3. **Bare-number log line.** One WARNING per reply, covering prose and Surface Text:
   `reply has numbers typed by the model: 9, 10`. `UiResult.bare_numbers` lists prose numbers
   first, then Surface Text numbers in component order.
4. **Blocked question.** A `litellm.ContentPolicyViolationError` from the model call ends the
   Turn with `CONTENT_POLICY_PROSE` = "The model provider declined to answer this question.
   Rephrasing it may help." It is not retried (it already isn't: status 400, see `_retryable`).
   Logging is unchanged (`model call failed: ContentPolicyViolationError status=400`).
5. **Harness.** `classify_failure(CONTENT_POLICY_PROSE)` is `content_policy`. `surface_summary`
   reports a metric only for a `Card` whose child `Column` holds a caption `Text` and an h2 `Text`
   (what `Metric` expands to). A bare `Column` with a caption and an h2 is not a metric. The bare
   numbers parser reads the new log wording.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| 1 | Placeholder in model-written Text is filled and escaped | `test_ui.py::test_surface_text_placeholder_is_filled` |
| 2 | Failing placeholder in Text → UiError naming the component | `test_ui.py::test_surface_text_placeholder_error_raises_ui_error` |
| 3 | Typed numbers in Surface Text are reported and logged | `test_ui.py::test_surface_text_bare_number_is_logged` |
| 4 | Macro output (formatted values) is not counted as bare numbers | `test_ui.py::test_macro_output_is_not_counted_as_bare_numbers` |
| 5 | Placeholders in each Macro's title/label are filled | `test_ui.py::test_macro_title_placeholder_is_filled[BarChart,LineChart,DataTable,Metric]` |
| 6 | Failing placeholder in a Macro label → UiError naming the Macro | `test_ui.py::test_macro_title_placeholder_error_raises_ui_error` |
| 7 | Prose bare numbers still logged (new wording) | `test_ui.py::test_bare_number_is_logged` (updated) |
| 7a | Prose numbers come first, then Surface Text, in one log line | `test_ui.py::test_prose_and_surface_bare_numbers_share_one_log_line` |
| 8 | Content-policy error → its own prose, one model call, no retry | `test_llm.py::test_content_policy_error_is_not_retried` |
| 9 | Other model errors keep `MODEL_FAILED_PROSE` | `test_llm.py::test_model_error` (unchanged) |
| 10 | Harness classifies the new prose as `content_policy` | `test_effort_check.py::test_classify_failure` (new case) |
| 11 | Harness: bare Column with caption + h2 is not a metric; Metric's Card is | `test_effort_check.py::test_surface_summary_metrics_come_from_cards` |
| 12 | Harness parses the new bare-number log wording | `test_effort_check.py` log-parse test (fixture line updated) |

## Decisions

- **Fill, don't forbid.** Plain `Text` stays in the catalog (headings, notes). See ADR 0013.
- **Warn only.** Bare numbers in Surface Text are logged, never a Repair (ADR 0001; a Repair costs a
  model round trip, and the regex matches years such as "2024-25").
- **Only model-written Text is scanned**, before macro expansion. Macro-built Text carries
  formatted values from Results and is never scanned. `Cite` is a Macro, not `Text`, so it is not
  double-filled.
- **Macro titles/labels are filled but not scanned** for bare numbers. Titles routinely hold years;
  scanning them adds noise. Non-goal for now.
- **No other Text-bearing components** are filled (e.g. `Tabs` titles). Non-goal.
- **The provider-agnostic message** names neither Meta nor a content filter, and doesn't say
  "try again".
- **Match the exception by type** (`litellm.ContentPolicyViolationError`), not by class name.
- **Old run files are not re-summarised.**
- **Prompt unchanged.** `instructions.md` already tells the model to use `Cite` on the Surface; filling
  Text is a safety net, not a new feature to advertise (and keeps the cached prefix as is).
- **Only filled values are escaped**, as in prose: the model's own text in a `Text` is left as
  written (amended during build; criterion 1's test expects `…\\.8M.` with an unescaped `.`).
- **The log wording is a shared constant** `ui.BARE_NUMBERS_LOG`, imported by the harness, so the two
  can't drift (amended during build).

## Test command

```bash
cd explorer/agent && uv run pytest tests/test_ui.py tests/test_llm.py tests/test_effort_check.py tests/test_macros.py -q
uv run pytest -q   # full suite
```
