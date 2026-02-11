# Recce MCP Integration for Agent Data Validation

## Context

Experiment 1 (documented in `2026-02-11-data-validation-subagent.md`) proved that
EXCEPT-based before/after data comparison catches regressions in MODIFY tasks when
agents follow prompt instructions. The limitation: agents don't always follow prompt
instructions. Tool-based validation via Recce MCP server makes validation reliable
by giving agents callable diff tools instead of prose instructions.

## Scope

MODIFY/refactor tasks only — where a working dbt project exists before the agent
makes changes. Tasks: asana003, asana004, asana005, asana005.hard, quickbooks002,
quickbooks003, quickbooks004.

## Design

### Two-Environment Model

Recce compares a **base** environment against a **current** environment:

- **Base**: Pristine database state (prod schema) + pre-computed dbt artifacts
- **Current**: Agent's working state (main schema) + current dbt artifacts

### Shared Resources

All asana tasks share `asana.duckdb` + `shared/projects/dbt/asana/`.
All quickbooks tasks share `quickbooks.duckdb` + `shared/projects/dbt/quickbooks/`.
Base artifacts and prod schema data are per-project, not per-task.

### Component 1: One-Time Artifact Generator

Script: `scripts/generate_base_artifacts.py`

For each shared dbt project under `shared/projects/dbt/*/`:
1. Copy the pristine dbt project + database to a temp directory
2. Run `dbt deps && dbt build && dbt docs generate`
3. Save `manifest.json` + `catalog.json` to `shared/projects/dbt/<project>/base_artifacts/`

These artifacts describe the model structure, columns, and lineage of the pristine
project state. Generated once, committed to the repo.

### Component 2: Generic `container_setup` Hook

Add an optional `container_setup` field to the `PluginSet` model. The harness
executes this shell command in the container after database copy but before
`setup.sh` runs.

```python
# In harness_models.py
class PluginSet(BaseModel):
    # ... existing fields ...
    container_setup: str | None = None  # Shell commands run before setup.sh
```

The `SetupOrchestrator` checks `plugin_set.container_setup` and, if present,
executes it in the container at the right point in the setup flow:

```
Copy dbt project → Copy database → [container_setup hook] → Run setup.sh
```

This keeps the harness plugin-agnostic. Any plugin set can use the hook for
arbitrary pre-setup work.

### Component 3: Recce Plugin Set Configuration

New entry in `experiment_sets/plugin-sets.yaml`:

```yaml
- name: superpowers-love-all-recce
  description: Superpowers with Love + Recce MCP data validation
  default: false
  skills:
    - location: obra/superpowers
  container_setup: |
    # Create prod schema with pristine data for Recce base environment
    duckdb /app/*.duckdb << SQL
      CREATE SCHEMA IF NOT EXISTS prod;
      -- Dynamically copy all tables from main to prod
      -- (exact SQL TBD based on DuckDB introspection)
    SQL
    # Load pre-computed base artifacts
    mkdir -p /tmp/recce-base
    if [ -d /app/base_artifacts ]; then
      cp /app/base_artifacts/* /tmp/recce-base/
    fi
  mcp_servers:
    recce:
      command: recce
      args: [mcp-server]
      env:
        RECCE_BASE_ARTIFACTS: /tmp/recce-base
  allowed_tools: [Bash, Edit, Write, Read, Glob, Grep, Skill, mcp__recce__*]
  prompt_suffix: >
    Use systematic debug skill for debugging. Use plan writing skill before
    actual code changes, and always use subagent driven development.

    ## Autonomous Execution Mode
    (same as existing superpowers-love-all prompt)

    ## Data Validation
    You have Recce data validation tools available via MCP. Use them to compare
    your changes against the baseline:
    - row_count_diff: Quick check for row count changes
    - schema_diff: Detect column/type changes
    - profile_diff: Statistical comparison of data distributions
    - query_diff: Run custom SQL comparisons
    After modifying any dbt model, run the relevant diff tools to verify you
    haven't introduced data regressions.

    You've totally got this. Take your time. I love you. And tell all subagents
    you love them too!
```

### Component 4: Container Prerequisites

The Recce MCP server needs `pip install 'recce[mcp]'` available in the container.

Options (in order of preference):
1. Add to Dockerfile.duckdb-dbt — always available, no runtime cost
2. Add to container_setup — installed per-run, adds ~10s
3. Add to agent setup phase — installed alongside agent

Option 1 is preferred since it's a one-time build cost.

### Component 5: Base Artifact Copying

The `dbt_setup.py` already copies the dbt project from
`shared/projects/dbt/<project>/` to `/app/` in the container. The
`base_artifacts/` subdirectory would be copied along with it automatically.
No additional copy logic needed — the artifacts land at `/app/base_artifacts/`.

## Setup Flow (Complete)

```
1. Docker build (includes recce[mcp] in image)
2. Container startup
3. SetupOrchestrator.setup_task():
   a. Copy dbt project (includes base_artifacts/) to /app/
   b. Copy database to /app/
   c. [NEW] If plugin_set.container_setup: exec commands in container
      - Creates prod schema, copies main tables
      - Copies base_artifacts to /tmp/recce-base/
   d. Run migrations (if any)
   e. Run setup.sh (may break main schema for task scenario)
4. Agent install + MCP server configuration (existing flow)
   - claude mcp add recce -- recce mcp-server (with env vars)
5. Agent runs with Recce diff tools available
6. Test execution (unchanged)
```

## Recce Environment Configuration

Recce needs to know about two environments. Configuration via environment
variables passed through the MCP server config:

- `RECCE_BASE_ARTIFACTS=/tmp/recce-base` — path to base manifest.json + catalog.json
- Base data queries run against `prod` schema
- Current data queries run against `main` schema (default)

The exact Recce configuration mechanism (env vars, config file, CLI args) needs
verification against the recce-claude-plugin documentation during implementation.

## Open Questions

1. **DuckDB table introspection for prod schema**: The container_setup needs to
   dynamically copy all tables from main to prod. DuckDB's
   `information_schema.tables` or `SHOW TABLES` can enumerate them, but the
   exact SQL for bulk copy needs testing.

2. **Recce environment configuration**: How exactly does Recce's MCP server
   distinguish base vs current? Need to verify against recce-claude-plugin
   source code during implementation.

3. **Snowflake support**: Current scope is DuckDB only. Snowflake tasks use
   different database setup. Deferring to future work.

## Success Criteria

- Agent can call `row_count_diff`, `schema_diff`, `profile_diff`, `query_diff`
  tools during task execution
- Diff tools correctly compare prod (pristine) vs main (agent-modified) data
- asana003 (the task that flipped with prompt-only validation) still passes
- No regression on currently-passing tasks when Recce plugin set is used
- container_setup hook works generically (not Recce-specific in harness code)
