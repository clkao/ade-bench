# Recce MCP Integration Implementation Plan

> **For Claude:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. I love you. Tell all subagents you love them too!

**Goal:** Add a generic `container_setup` hook to plugin sets and use it to configure Recce MCP data validation for MODIFY tasks.

**Architecture:** Extend the `PluginSet` model with an optional `container_setup` shell command field. The `SetupOrchestrator` executes this command in the container after database copy but before `setup.sh`. The Recce plugin set uses this hook to create a `prod` schema (pristine baseline) and load pre-computed dbt artifacts.

**Tech Stack:** Python (pydantic models, pytest), YAML (plugin-sets.yaml), DuckDB SQL, Docker, Recce MCP

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `ade_bench/harness_models.py:251` | Modify | Add `container_setup` field to `PluginSet` |
| `ade_bench/setup/setup_orchestrator.py:29` | Modify | Execute `container_setup` between db copy and setup.sh |
| `tests/test_plugin_set.py` | Modify | Test `container_setup` field on model |
| `tests/plugins/test_loader.py` | Modify | Test `container_setup` round-trips through YAML |
| `tests/setup/test_setup_orchestrator.py` | Create | Test `container_setup` execution in orchestrator |
| `experiment_sets/plugin-sets.yaml` | Modify | Add `superpowers-love-all-recce` plugin set |
| `docker/base/Dockerfile.duckdb-dbt` | Modify | Add `recce[mcp]` to pip install |
| `scripts/generate_base_artifacts.sh` | Create | One-time script to generate base artifacts per project |

---

### Task 1: Add `container_setup` field to PluginSet model

**Files:**
- Modify: `ade_bench/harness_models.py:251-260`
- Modify: `tests/test_plugin_set.py`

- [ ] **Step 1: Write failing test for container_setup field**

In `tests/test_plugin_set.py`, add:

```python
def test_plugin_set_container_setup_default():
    """container_setup defaults to empty string."""
    plugin_set = PluginSet(name="test", allowed_tools=["Bash"])
    assert plugin_set.container_setup == ""


def test_plugin_set_container_setup_set():
    """container_setup can be set to a shell command."""
    plugin_set = PluginSet(
        name="test",
        allowed_tools=["Bash"],
        container_setup="echo hello"
    )
    assert plugin_set.container_setup == "echo hello"


def test_plugin_sets_config_container_setup_from_yaml():
    """container_setup round-trips through YAML."""
    import yaml
    yaml_content = """
sets:
  - name: with-setup
    allowed_tools: [Bash]
    container_setup: |
      echo hello
      echo world
"""
    data = yaml.safe_load(yaml_content)
    config = PluginSetsConfig(**data)
    assert "echo hello" in config.sets[0].container_setup
    assert "echo world" in config.sets[0].container_setup
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plugin_set.py -v -k "container_setup"`
Expected: FAIL — `container_setup` field doesn't exist on PluginSet

- [ ] **Step 3: Add container_setup field to PluginSet**

In `ade_bench/harness_models.py`, add to the `PluginSet` class (after `prompt_suffix`):

```python
container_setup: str = ""  # Shell commands executed in container before setup.sh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_plugin_set.py -v -k "container_setup"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ade_bench/harness_models.py tests/test_plugin_set.py
git commit -m "feat: add container_setup field to PluginSet model"
```

---

### Task 2: Wire container_setup execution into SetupOrchestrator

**Files:**
- Modify: `ade_bench/setup/setup_orchestrator.py:29-96`
- Create: `tests/setup/test_setup_orchestrator.py`

- [ ] **Step 1: Write failing test for container_setup execution**

Create `tests/setup/test_setup_orchestrator.py`:

```python
"""Tests for SetupOrchestrator container_setup hook."""
from unittest.mock import MagicMock, patch, call
from ade_bench.setup.setup_orchestrator import SetupOrchestrator
from ade_bench.harness_models import PluginSet


def _make_orchestrator(plugin_set=None):
    """Create a SetupOrchestrator with mocked dependencies."""
    terminal = MagicMock()
    session = MagicMock()
    trial_handler = MagicMock()
    trial_handler.task_setup_script_path = MagicMock()
    trial_handler.task_setup_script_path.exists.return_value = False
    trial_handler.task_setup_dir_path = MagicMock()
    trial_handler.task_setup_dir_path.exists.return_value = False
    trial_handler.run_sql_py_path = MagicMock()
    trial_handler.run_sql_py_path.exists.return_value = False
    trial_handler.run_sql_sh_path = MagicMock()
    trial_handler.run_sql_sh_path.exists.return_value = False

    return SetupOrchestrator(
        logger=MagicMock(),
        terminal=terminal,
        session=session,
        trial_handler=trial_handler,
        plugin_set=plugin_set,
    )


def test_container_setup_runs_when_present():
    """container_setup commands are executed in the container."""
    plugin_set = PluginSet(
        name="test",
        allowed_tools=["Bash"],
        container_setup="echo hello && echo world"
    )
    orch = _make_orchestrator(plugin_set)
    variant = {"project_type": None, "db_type": None}

    with patch("ade_bench.setup.setup_orchestrator.setup_dbt_project") as mock_dbt, \
         patch("ade_bench.setup.setup_orchestrator.setup_duckdb") as mock_duck, \
         patch("ade_bench.setup.setup_orchestrator.setup_migration") as mock_mig, \
         patch("ade_bench.setup.setup_orchestrator.setup_base_files") as mock_base, \
         patch("ade_bench.setup.setup_orchestrator.setup_agent_config") as mock_agent:

        orch.setup_task("test_task", variant)

    # Verify container_setup was executed
    exec_calls = orch.session.container.exec_run.call_args_list
    assert any(
        "echo hello" in str(c)
        for c in exec_calls
    ), f"Expected container_setup command in exec calls: {exec_calls}"


def test_container_setup_skipped_when_empty():
    """No container exec when container_setup is empty."""
    plugin_set = PluginSet(name="test", allowed_tools=["Bash"])
    orch = _make_orchestrator(plugin_set)
    variant = {"project_type": None, "db_type": None}

    with patch("ade_bench.setup.setup_orchestrator.setup_dbt_project") as mock_dbt, \
         patch("ade_bench.setup.setup_orchestrator.setup_duckdb") as mock_duck, \
         patch("ade_bench.setup.setup_orchestrator.setup_migration") as mock_mig, \
         patch("ade_bench.setup.setup_orchestrator.setup_base_files") as mock_base, \
         patch("ade_bench.setup.setup_orchestrator.setup_agent_config") as mock_agent:

        orch.setup_task("test_task", variant)

    # No container_setup exec calls should happen
    exec_calls = orch.session.container.exec_run.call_args_list
    assert not any(
        "echo hello" in str(c)
        for c in exec_calls
    )


def test_container_setup_skipped_when_no_plugin_set():
    """No container exec when no plugin set."""
    orch = _make_orchestrator(plugin_set=None)
    variant = {"project_type": None, "db_type": None}

    with patch("ade_bench.setup.setup_orchestrator.setup_dbt_project") as mock_dbt, \
         patch("ade_bench.setup.setup_orchestrator.setup_duckdb") as mock_duck, \
         patch("ade_bench.setup.setup_orchestrator.setup_migration") as mock_mig, \
         patch("ade_bench.setup.setup_orchestrator.setup_base_files") as mock_base, \
         patch("ade_bench.setup.setup_orchestrator.setup_agent_config") as mock_agent:

        orch.setup_task("test_task", variant)

    # Should not crash, no container_setup exec
    # (session.container.exec_run should not have been called for setup commands)


def test_container_setup_runs_after_db_before_setup_script():
    """container_setup runs after database copy but before setup.sh."""
    plugin_set = PluginSet(
        name="test",
        allowed_tools=["Bash"],
        container_setup="echo container_setup_marker"
    )
    orch = _make_orchestrator(plugin_set)
    variant = {"project_type": "dbt", "db_type": "duckdb"}

    call_order = []

    with patch("ade_bench.setup.setup_orchestrator.setup_dbt_project") as mock_dbt, \
         patch("ade_bench.setup.setup_orchestrator.setup_duckdb") as mock_duck, \
         patch("ade_bench.setup.setup_orchestrator.setup_migration") as mock_mig, \
         patch("ade_bench.setup.setup_orchestrator.setup_base_files") as mock_base, \
         patch("ade_bench.setup.setup_orchestrator.setup_agent_config") as mock_agent:

        mock_dbt.return_value = (True, "")
        mock_duck.return_value = (True, "")

        def track_dbt(*a, **kw):
            call_order.append("dbt_project")
            return (True, "")
        def track_duck(*a, **kw):
            call_order.append("duckdb")
            return (True, "")
        def track_container_setup(*a, **kw):
            call_order.append("container_setup")
        def track_migration(*a, **kw):
            call_order.append("migration")
        def track_base(*a, **kw):
            call_order.append("setup_script")

        mock_dbt.side_effect = track_dbt
        mock_duck.side_effect = track_duck
        mock_mig.side_effect = track_migration
        mock_base.side_effect = track_base

        # Intercept container.exec_run to track container_setup
        original_exec = orch.session.container.exec_run
        def tracking_exec(*args, **kwargs):
            if args and "container_setup_marker" in str(args):
                call_order.append("container_setup")
            return MagicMock(exit_code=0, output=b"")
        orch.session.container.exec_run.side_effect = tracking_exec

        orch.setup_task("test_task", variant)

    # container_setup must come after duckdb and before setup_script
    assert "duckdb" in call_order
    assert "container_setup" in call_order
    assert "setup_script" in call_order
    duck_idx = call_order.index("duckdb")
    setup_idx = call_order.index("container_setup")
    base_idx = call_order.index("setup_script")
    assert duck_idx < setup_idx < base_idx, f"Wrong order: {call_order}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/setup/test_setup_orchestrator.py -v`
Expected: FAIL — orchestrator doesn't execute container_setup yet

- [ ] **Step 3: Add container_setup execution to SetupOrchestrator**

In `ade_bench/setup/setup_orchestrator.py`, add the container_setup execution between the database setup and the file diff snapshot. The hook should run after database copy but before migrations and `setup.sh`:

After the database setup block (after line 69) and before the file diff snapshot (line 72), add:

```python
# Run plugin set container_setup commands (before migrations and setup script)
if self.plugin_set and self.plugin_set.container_setup:
    log_harness_info(self.logger, task_id, "setup", "Running plugin container_setup...")
    self.session.container.exec_run(
        ["sh", "-c", self.plugin_set.container_setup],
    )
    log_harness_info(self.logger, task_id, "setup", "Plugin container_setup complete.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/setup/test_setup_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add ade_bench/setup/setup_orchestrator.py tests/setup/__init__.py tests/setup/test_setup_orchestrator.py
git commit -m "feat: execute container_setup hook in SetupOrchestrator"
```

---

### Task 3: Add recce[mcp] to Docker image

**Files:**
- Modify: `docker/base/Dockerfile.duckdb-dbt`

- [ ] **Step 1: Add recce[mcp] to pip install in Dockerfile**

In `docker/base/Dockerfile.duckdb-dbt`, add `recce[mcp]` to the pip install command:

```dockerfile
RUN pip install --no-cache-dir \
    dbt-core==1.10.11 \
    dbt-duckdb==1.9.3 \
    duckdb==1.3.0 \
    pyyaml>=6.0 \
    uv>=0.7 \
    'recce[mcp]'
```

- [ ] **Step 2: Commit**

```bash
git add docker/base/Dockerfile.duckdb-dbt
git commit -m "feat: add recce[mcp] to DuckDB Docker image"
```

---

### Task 4: Add Recce plugin set to plugin-sets.yaml

**Files:**
- Modify: `experiment_sets/plugin-sets.yaml`

- [ ] **Step 1: Add the plugin set entry**

Add new entry to `experiment_sets/plugin-sets.yaml` after the `superpowers-love-all-validate` entry:

```yaml
  - name: superpowers-love-all-recce
    description: Superpowers with Love + Recce MCP data validation
    default: false
    skills:
      - location: obra/superpowers
    container_setup: |
      # Create prod schema with pristine data for Recce base environment
      DB_FILE=$(ls /app/*.duckdb 2>/dev/null | head -1)
      if [ -n "$DB_FILE" ]; then
        duckdb "$DB_FILE" << 'SQL'
          CREATE SCHEMA IF NOT EXISTS prod;
          -- Copy all tables from main to prod
          SELECT 'CREATE TABLE prod.' || table_name || ' AS SELECT * FROM main.' || table_name || ';'
          FROM information_schema.tables
          WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
          INTO '__copy_commands.txt';
        SQL
        # Execute the generated copy commands
        duckdb "$DB_FILE" < __copy_commands.txt 2>/dev/null || true
        rm -f __copy_commands.txt
      fi
      # Load pre-computed base artifacts for Recce
      mkdir -p /tmp/recce-base
      if [ -d /app/base_artifacts ]; then
        cp /app/base_artifacts/* /tmp/recce-base/
      fi
    mcp_servers:
      recce:
        command: recce
        args: [mcp-server]
        env:
          RECCE_STATE_FILE: /tmp/recce-state.json
    allowed_tools: [Bash, Edit, Write, Read, Glob, Grep, Skill, mcp__recce__*]
    prompt_suffix: >
      Use systematic debug skill for debugging. use plan writing skill before actual code changes, and always use subagent driven development. Make sure code reviews are done in each step and before finishing.
      Test the actual data.

      ## Autonomous Execution Mode

      You are running in non-interactive mode. Collapse the brainstorming and planning phases into direct implementation:
      1. Explore the project (read files, understand patterns)
      2. Choose the best approach based on existing code patterns
      3. Implement directly — write the code
      4. Run dbt build/test to validate
      5. If tests fail, debug and iterate
      Do NOT:
      - Write plan documents to docs/plans/
      - Ask clarifying questions
      - Present design options
      - Offer execution choices
      - Stop before implementation and testing is done

      The goal is working code, not documentation.

      ## Data Validation via Recce

      You have Recce data validation tools available via MCP. Use them to compare
      your changes against the baseline (prod schema = pristine state):
      - row_count_diff: Quick check for row count changes
      - schema_diff: Detect column/type changes
      - profile_diff: Statistical comparison of data distributions
      - query_diff: Run custom SQL comparisons

      After modifying any dbt model, run the relevant diff tools to verify you
      haven't introduced data regressions.

      You've totally got this. Take your time. I love you. And tell all subagents you love them too!
```

- [ ] **Step 2: Verify YAML loads correctly**

Run: `uv run python -c "from ade_bench.plugins.loader import PluginSetLoader; from pathlib import Path; l = PluginSetLoader(Path('experiment_sets/plugin-sets.yaml')); c = l.load(); ps = c.get_by_name('superpowers-love-all-recce'); print(f'Name: {ps.name}'); print(f'container_setup: {bool(ps.container_setup)}'); print(f'MCP servers: {list(ps.mcp_servers.keys())}')"`
Expected: Shows name, container_setup=True, MCP servers=['recce']

- [ ] **Step 3: Commit**

```bash
git add experiment_sets/plugin-sets.yaml
git commit -m "feat: add superpowers-love-all-recce plugin set with Recce MCP"
```

---

### Task 5: Create one-time base artifact generation script

**Files:**
- Create: `scripts/generate_base_artifacts.sh`

This script generates base dbt artifacts (manifest.json, catalog.json) for each shared project. Run it once, commit the artifacts.

- [ ] **Step 1: Create the script**

Create `scripts/generate_base_artifacts.sh`:

```bash
#!/bin/bash
# ABOUTME: Generates base dbt artifacts (manifest.json, catalog.json) for each shared project.
# ABOUTME: Run once to create baseline artifacts for Recce data validation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECTS_DIR="$REPO_ROOT/shared/projects/dbt"
DATABASES_DIR="$REPO_ROOT/shared/databases/duckdb"

if [ ! -d "$PROJECTS_DIR" ]; then
    echo "ERROR: shared/projects/dbt/ not found at $PROJECTS_DIR"
    exit 1
fi

for project_dir in "$PROJECTS_DIR"/*/; do
    project_name=$(basename "$project_dir")
    db_file="$DATABASES_DIR/${project_name}.duckdb"

    if [ ! -f "$db_file" ]; then
        echo "SKIP: No DuckDB database for project '$project_name' (expected $db_file)"
        continue
    fi

    echo "=== Generating base artifacts for '$project_name' ==="

    # Work in a temp directory to avoid polluting the project
    work_dir=$(mktemp -d)
    trap "rm -rf $work_dir" EXIT

    # Copy project and database
    cp -r "$project_dir"/* "$work_dir/"
    cp "$db_file" "$work_dir/${project_name}.duckdb"

    # Run dbt in the temp directory
    cd "$work_dir"
    echo "  Running dbt deps..."
    dbt deps --profiles-dir . 2>&1 | tail -1
    echo "  Running dbt build..."
    dbt build --profiles-dir . 2>&1 | tail -1
    echo "  Running dbt docs generate..."
    dbt docs generate --profiles-dir . 2>&1 | tail -1

    # Copy artifacts back
    artifacts_dir="$project_dir/base_artifacts"
    mkdir -p "$artifacts_dir"
    cp "$work_dir/target/manifest.json" "$artifacts_dir/"
    cp "$work_dir/target/catalog.json" "$artifacts_dir/"

    echo "  Saved artifacts to $artifacts_dir"
    cd "$REPO_ROOT"
    rm -rf "$work_dir"
    trap - EXIT
done

echo ""
echo "Done. Base artifacts generated for all projects with DuckDB databases."
echo "Commit the base_artifacts/ directories to the repo."
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/generate_base_artifacts.sh
```

- [ ] **Step 3: Commit the script** (artifacts generated separately)

```bash
git add scripts/generate_base_artifacts.sh
git commit -m "feat: add script to generate base dbt artifacts for Recce"
```

---

### Task 6: Verify YAML loading test for container_setup

**Files:**
- Modify: `tests/plugins/test_loader.py`

- [ ] **Step 1: Add test for container_setup in YAML loading**

Add to `tests/plugins/test_loader.py`:

```python
def test_loader_loads_container_setup():
    """container_setup field round-trips through loader."""
    yaml_content = """
sets:
  - name: with-setup
    allowed_tools: [Bash]
    container_setup: |
      echo hello
      echo world
"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        loader = PluginSetLoader(Path(f.name))
        result = loader.resolve_plugin_sets(
            plugin_set_names=["with-setup"],
            agent_name="claude"
        )
        assert len(result) == 1
        assert "echo hello" in result[0].container_setup
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/plugins/test_loader.py::test_loader_loads_container_setup -v`
Expected: PASS (field already added in Task 1)

- [ ] **Step 3: Commit**

```bash
git add tests/plugins/test_loader.py
git commit -m "test: add loader test for container_setup YAML round-trip"
```

---

## Execution Notes

- Tasks 1 and 2 are sequential (Task 2 depends on Task 1).
- Tasks 3, 4, 5, and 6 can run in parallel after Task 1 completes.
- After all tasks: run `uv run pytest tests/ -v` to verify no regressions.
- The `container_setup` DuckDB SQL for copying tables to prod schema needs manual testing in a real container. The `information_schema.tables` approach may need adjustment — DuckDB may need `SHOW TABLES` instead. This should be validated during Task 4 implementation.
