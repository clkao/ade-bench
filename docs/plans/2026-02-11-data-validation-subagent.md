# Data Validation During Agent Code Changes

Investigating whether a data validation mechanism can improve agent benchmark scores
by catching data regressions and logic errors during dbt model changes.

## Background

Analysis of Opus superpowers-love-all results (35/48, 72.9%) showed 13 failures.
Failure mode categorization:

| Category | Count | Example |
|---|---|---|
| Wrong data values / join logic | 5-6 | asana004: wrong join direction drops 3 rows |
| Schema / structural errors | 2-3 | analytics_engineering006: missing JOIN, wrong columns |
| Ignored build errors | 1 | quickbooks003: agent ignored compilation errors |
| Task execution gaps | 2 | f1001: never created required files |

~9/13 failures could theoretically be caught by data validation. Two sub-categories:

- **MODIFY tasks** (refactoring existing models): before/after comparison catches regressions
- **CREATE tasks** (new models from scratch): no "before" state; needs independent semantic validation

## Experiment 1: Prompt-Only Before/After Validation

### Hypothesis

Adding a "Data Validation Protocol" to the prompt suffix will cause agents to
catch data regressions during refactoring tasks.

### Approach

New plugin set `superpowers-love-all-validate` — identical to `superpowers-love-all`
plus a Data Validation Protocol block in the prompt suffix.

### Iteration 1: Row Count Only

Prompt instructed agent to run `SELECT COUNT(*)` before/after changes.

**Result on asana004: FAIL (6/7)**

The agent ran counts and saw 16 rows before and after — row count was preserved
because `asana__project` uses `LEFT JOIN` + `COALESCE(x, 0)`. The actual bug changed
*values* (number_of_users_involved: 1→0 for 13 rows) while preserving row count.

**Learning:** Row count is a useful short-circuit (cheap, catches obvious breaks)
but insufficient for value-level regressions.

### Iteration 2: EXCEPT-Based Value Comparison

Prompt instructed agent to:
1. Before changes: `COPY (SELECT * FROM model ORDER BY 1) TO '/tmp/before_model.csv' (HEADER);`
2. Make changes, run `dbt build`
3. After: `SELECT * FROM model EXCEPT SELECT * FROM read_csv('/tmp/before_model.csv');`
4. Also reverse: `SELECT * FROM read_csv(...) EXCEPT SELECT * FROM model;`

**Result on asana004: FAIL (6/7) — but validation worked!**

The agent:
1. Saved baseline to CSV before changes
2. Ran EXCEPT after changes → detected `DATA INTEGRITY: FAIL` (13 rows changed)
3. Correctly identified root cause (wrong join direction in intermediate model)
4. Fixed the join direction
5. Re-validated → `DATA INTEGRITY: PASS`

The downstream model (`asana__project`) was correctly preserved. The remaining
failure was on `AUTO_int_asana__project_user_agg_equality` — the **new** intermediate
model's independent correctness, not a regression. This is a CREATE-class validation
problem, outside the scope of before/after comparison.

### Broader Test: 4 MODIFY Tasks

Ran asana003, asana005, asana005.hard, quickbooks003 with the validation prompt.

| Task | Baseline (7 runs) | With Validation | Notes |
|---|---|---|---|
| **asana003** | FAIL in 7/7 runs (17/18) | **PASS (18/18)** | Flipped! ref→source refactoring validated |
| asana004 | FAIL in 7/7 runs (6/7) | FAIL (6/7) | Downstream fixed; new intermediate model spec fails |
| asana005 | FAIL in 7/7 runs (8/9) | FAIL (8/9) | Same pattern as asana004 |
| asana005.hard | FAIL in 7/7 runs (8/9) | FAIL (8/9) | Same pattern as asana004 |
| quickbooks003 | FAIL in 7/7 runs (6/15) | FAIL (6/15) | Agent still ignores compilation errors |

**Net result: 1 task flipped from always-fail to pass. 2 tasks had downstream
regressions fixed but fail on new model spec. 1 task unchanged.**

### Key Findings

1. **Before/after EXCEPT comparison works for refactoring validation.** When the agent
   follows the protocol, it reliably detects value-level regressions and can fix them.

2. **Row count alone is insufficient.** LEFT JOIN + COALESCE patterns preserve row
   counts while silently corrupting values.

3. **The protocol doesn't help CREATE tasks.** New models have no "before" state.
   Independent semantic validation (derived from task description) is needed.

4. **The protocol doesn't help when the agent ignores build errors.** quickbooks003
   fails because the agent doesn't recognize compilation errors as blocking issues,
   not because it lacks validation tools.

5. **Persistence via filesystem works.** `COPY TO` + `read_csv()` in DuckDB handles
   the before-state persistence without flooding the agent's context window.

## Open Questions

- **Recce MCP integration**: Recce handles before/after dbt artifact comparison
  externally. Could offload the validation to a tool rather than relying on prompt
  instructions. Requires exploring setup (dbt artifacts, db schema config).

- **CREATE task validation**: How to validate new models without expected output?
  Options: task-prompt-driven semantic checks (LLM reads requirements, generates
  assertions), domain-knowledge sanity bounds, schema completeness checks.

- **Build enforcement**: quickbooks003 needs "all models must compile with zero
  errors" as a hard gate, not just data comparison.

- **Cost/context impact**: The validation protocol adds ~$0.50-1.00 and 5-10 turns
  per task. Acceptable for Opus, may be expensive for weaker models.

- **Full suite regression test**: Need to run all 48 tasks with the validation prompt
  to verify no regressions on currently-passing tasks.
