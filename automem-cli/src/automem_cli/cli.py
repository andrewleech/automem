"""Main CLI interface for AutoMem"""

import sys
from pathlib import Path
from typing import Optional

import click
import httpx

from . import __version__
from .api import AutoMemClient
from .config import Config
from .output import (
    output_error,
    output_info,
    output_json,
    output_rich,
    output_success,
    output_warning,
)

# Configure Click to use -h for help
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.option("--endpoint", envvar="AUTOMEM_ENDPOINT", help="API endpoint")
@click.option("--token", envvar="AUTOMEM_API_TOKEN", help="API token")
@click.option("--project", envvar="AUTOMEM_PROJECT_ID", help="Project ID")
@click.version_option(version=__version__, prog_name="am")
@click.pass_context
def main(ctx, json_mode, endpoint, token, project):
    """AutoMem CLI - Agent-first memory management

    Use --json flag for programmatic output on any command.
    """
    ctx.ensure_object(dict)

    # Load config
    config = Config.load()

    # Override with CLI options if provided
    if endpoint:
        config.endpoint = endpoint
    if token:
        config.api_token = token
    if project:
        config.project_id = project

    ctx.obj["config"] = config
    ctx.obj["json_mode"] = json_mode

    # Create client (will be used by subcommands)
    try:
        ctx.obj["client"] = AutoMemClient(config)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e), "success": False})
        else:
            output_error(f"Failed to create client: {e}")
        sys.exit(1)


@main.command()
@click.argument("content")
@click.option("-t", "--type", "memory_type", help="Memory type")
@click.option("-p", "--priority", type=float, help="Importance/priority (0.0-1.0)")
@click.option("--tag", "tags_list", multiple=True, help="Tags (repeatable: --tag foo --tag bar)")
@click.option("--tags", "tags_csv", help="Tags (comma-separated: tag1,tag2,tag3)")
@click.option("--note", help="Additional note/annotation (stored in metadata)")
@click.option("--discovered-from", help="Memory ID this was discovered from")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def store(ctx, json_mode, content, memory_type, priority, tags_list, tags_csv, note, discovered_from):
    """Store a memory

    Examples:
        am store "User prefers dark mode" -t preference -p 0.8

        am store "Fixed auth bug" -t insight -p 0.9 --discovered-from bd-42

        am store "API redesign decision" -t decision -p 1.0 --tag api --tag architecture

        am store "Deploy to prod Friday 2pm" -t context -p 0.7 --tags deploy,production,scheduling

        am store "Bug fix for race condition" -t insight -p 0.9 --note "Affects user login flow"
    """
    client = ctx.obj["client"]
    # Use local json_mode parameter, fallback to global if not provided
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    # Merge tags from both --tag and --tags options
    all_tags = list(tags_list) if tags_list else []
    if tags_csv:
        all_tags.extend([t.strip() for t in tags_csv.split(",") if t.strip()])

    # Build metadata
    metadata = {}
    if discovered_from:
        metadata["discovered_from"] = discovered_from
    if note:
        metadata["note"] = note

    try:
        # Store the memory
        result = client.store_memory(
            content=content,
            memory_type=memory_type,
            importance=priority,
            tags=all_tags if all_tags else None,
            metadata=metadata if metadata else None,
        )

        # Create discovered-from relationship if specified
        relationship_created = False
        relationship_error = None
        memory_id = result.get("memory_id") or result.get("id")
        if discovered_from and memory_id:
            try:
                # Use DERIVED_FROM with correct direction: source -> new memory
                client.create_association(
                    discovered_from, memory_id, relation_type="DERIVED_FROM"
                )
                relationship_created = True
            except Exception as e:
                relationship_error = str(e)
                # Don't fail the whole operation if relationship creation fails
                if not json_mode:
                    output_warning(f"Failed to create discovery relationship: {e}")

        if json_mode:
            # Include relationship result in JSON output
            if discovered_from:
                result["relationship"] = {
                    "created": relationship_created,
                    "error": relationship_error
                }
            output_json(result)
        else:
            output_success(f"Stored memory {memory_id or 'unknown'}")
            if discovered_from and relationship_created:
                output_info(f"Linked to {discovered_from} (derived-from)")

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code})
        else:
            if e.response.status_code == 401:
                output_error("Authentication failed")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
            else:
                output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError as e:
        if json_mode:
            output_json({"error": "Connection refused", "endpoint": client.config.endpoint})
        else:
            output_error(f"Cannot connect to {client.config.endpoint}")
            output_info("Check that the AutoMem server is running")
        sys.exit(1)
    except (httpx.RemoteProtocolError, ConnectionResetError) as e:
        if json_mode:
            output_json({"error": "Connection reset by server"})
        else:
            output_error("Connection rejected by server")
            if not client.config.api_token:
                output_warning("No API token configured")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.argument("query", required=False, default="")
@click.option("-l", "--limit", type=int, default=10, help="Maximum results (default 10, server allows up to 500 via X-Max-Results header)")
@click.option("--importance-min", type=float, help="Minimum importance filter")
@click.option("--importance-max", type=float, help="Maximum importance filter")
@click.option("--tag", "tags", multiple=True, help="Filter by tags")
@click.option("-t", "--type", "memory_type", help="Filter by type")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def recall(ctx, json_mode, query, limit, importance_min, importance_max, tags, memory_type):
    """Recall memories

    Examples:
        am recall "authentication"

        am recall --importance-min 0.8 --limit 5

        am recall --tag api --type decision

        am recall "user preferences" --json | jq '.[].content'

        am recall --limit 200  # Request more than default 50 limit
    """
    client = ctx.obj["client"]
    # Use local json_mode parameter, fallback to global if not provided
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    try:
        result = client.recall_memories(
            query=query,
            limit=limit,
            importance_min=importance_min,
            importance_max=importance_max,
            tags=list(tags) if tags else None,
            memory_type=memory_type,
        )

        memories = result.get("results", [])

        if json_mode:
            output_json(memories)
        else:
            if not memories:
                output_info("No memories found")
            else:
                count = len(memories)
                output_success(f"Found {count} {'memory' if count == 1 else 'memories'}")
                output_rich("", memories)

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code})
        else:
            if e.response.status_code == 401:
                output_error("Authentication failed")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
            else:
                output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError as e:
        if json_mode:
            output_json({"error": "Connection refused", "endpoint": client.config.endpoint})
        else:
            output_error(f"Cannot connect to {client.config.endpoint}")
            output_info("Check that the AutoMem server is running")
        sys.exit(1)
    except (httpx.RemoteProtocolError, ConnectionResetError) as e:
        if json_mode:
            output_json({"error": "Connection reset by server"})
        else:
            output_error("Connection rejected by server")
            if not client.config.api_token:
                output_warning("No API token configured")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def health(ctx, json_mode):
    """Check API connectivity and health

    Examples:
        am health
        am health --json
    """
    client = ctx.obj["client"]
    config = ctx.obj["config"]
    # Use local json_mode parameter, fallback to global if not provided
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    try:
        result = client.health_check()

        if json_mode:
            output_json(result)
        else:
            status = result.get("status", "unknown")
            output_success(f"Connected to {config.endpoint}")
            output_info(f"Status: {status}")

            # Show database connectivity if available
            if "falkordb" in result:
                falkor_status = "✓" if result["falkordb"] else "✗"
                output_info(f"FalkorDB: {falkor_status}")
            if "qdrant" in result:
                qdrant_status = "✓" if result["qdrant"] else "✗"
                output_info(f"Qdrant: {qdrant_status}")

            # Show auth configuration
            if "auth" in result:
                auth = result["auth"]
                server_requires_auth = auth.get("required", False)
                client_has_token = bool(config.api_token)

                if server_requires_auth:
                    if client_has_token:
                        output_info(f"Auth: Server requires token, client configured ✓")
                    else:
                        output_warning(f"Auth: Server requires token, client NOT configured ✗")
                        output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
                else:
                    if client_has_token:
                        output_info(f"Auth: Server open (no token required)")
                    else:
                        output_info(f"Auth: No authentication configured")

            # Show project stats if available
            if "project" in result:
                project = result["project"]
                output_info(f"Project: {project.get('id', 'unknown')}")
                output_info(f"  Memories: {project.get('memories', 0)}")
                if project.get("patterns", 0) > 0:
                    output_info(f"  Patterns: {project.get('patterns', 0)}")

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code, "success": False})
        else:
            if e.response.status_code == 401:
                output_error("Authentication failed")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
            else:
                output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError as e:
        if json_mode:
            output_json({"error": "Connection refused", "endpoint": config.endpoint, "success": False})
        else:
            output_error(f"Cannot connect to {config.endpoint}")
            output_info("Check that the AutoMem server is running")
            output_info("Set AUTOMEM_ENDPOINT or run 'am init' to configure")
        sys.exit(1)
    except (httpx.RemoteProtocolError, ConnectionResetError) as e:
        if json_mode:
            output_json({"error": "Connection reset by server", "success": False})
        else:
            output_error("Connection rejected by server")
            if not config.api_token:
                output_warning("No API token configured")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
            else:
                output_info(f"Server at {config.endpoint} reset the connection")
                output_info("Check server logs or verify your API token is correct")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e), "success": False})
        else:
            output_error(f"Connection failed: {e}")
            if not config.api_token:
                output_warning("No API token configured")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
        sys.exit(1)


@main.command()
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def startup(ctx, json_mode):
    """Recall critical context for session startup

    Retrieves:
    - Critical lessons (tags: critical, lesson, ai-assistant)
    - System rules (tags: system, memory-recall)
    - 5 most recent memories

    Example:
        am startup --json
    """
    client = ctx.obj["client"]
    # Use local json_mode parameter, fallback to global if not provided
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    try:
        result = client.startup_recall()

        if json_mode:
            output_json(result)
        else:
            # Display three sections in rich terminal format
            critical_lessons = result.get("critical_lessons", [])
            system_rules = result.get("system_rules", [])
            recent_memories = result.get("recent_memories", [])

            output_success(result.get("summary", "Session context loaded"))

            if critical_lessons:
                output_info(f"\n📚 Critical Lessons ({len(critical_lessons)}):")
                output_rich("", critical_lessons)

            if system_rules:
                output_info(f"\n⚙️  System Rules ({len(system_rules)}):")
                output_rich("", system_rules)

            if recent_memories:
                output_info(f"\n🕐 Recent Memories ({len(recent_memories)}):")
                output_rich("", recent_memories)

            if not critical_lessons and not system_rules and not recent_memories:
                output_warning("No startup context found")
                output_info("Store memories with tags: critical, lesson, system, or ai-assistant")

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code})
        else:
            if e.response.status_code == 401:
                output_error("Authentication failed")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
            else:
                output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError as e:
        if json_mode:
            output_json({"error": "Connection refused", "endpoint": client.config.endpoint})
        else:
            output_error(f"Cannot connect to {client.config.endpoint}")
            output_info("Check that the AutoMem server is running")
        sys.exit(1)
    except (httpx.RemoteProtocolError, ConnectionResetError) as e:
        if json_mode:
            output_json({"error": "Connection reset by server"})
        else:
            output_error("Connection rejected by server")
            if not client.config.api_token:
                output_warning("No API token configured")
                output_info("Set AUTOMEM_API_TOKEN or run 'am init' to configure")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.option("--endpoint", prompt="API endpoint", default="http://localhost:8001")
@click.option("--token", prompt="API token (or leave empty for env var)", default="")
@click.option("--project", prompt="Project ID", default=lambda: Path.cwd().name)
def init(endpoint, token, project):
    """Initialize AutoMem workspace

    Creates .automem/config.yml in the current directory.
    Project ID defaults to current folder name.

    Example:
        am init
    """
    config = Config(
        endpoint=endpoint,
        api_token=token if token else None,
        project_id=project,
    )

    try:
        config_file = config.save()
        output_success(f"Initialized {config_file}")

        # Validate configuration with health check
        output_info("Checking connection...")
        try:
            client = AutoMemClient(config)
            result = client.health_check()

            status = result.get("status", "unknown")
            output_success(f"Connected to {config.endpoint}")
            output_info(f"Status: {status}")

            # Show database connectivity
            if "falkordb" in result:
                falkor_status = "✓" if result["falkordb"] else "✗"
                output_info(f"FalkorDB: {falkor_status}")
            if "qdrant" in result:
                qdrant_status = "✓" if result["qdrant"] else "✗"
                output_info(f"Qdrant: {qdrant_status}")

            output_info("Next: Ask your AI agent to run 'am onboard' and follow the integration guidance")

        except httpx.HTTPStatusError as e:
            output_warning(f"Health check failed: HTTP {e.response.status_code}")
            output_info("Check your endpoint and token settings")
        except Exception as e:
            output_warning(f"Health check failed: {e}")
            output_info("Check your endpoint and token settings")

    except Exception as e:
        output_error(f"Failed to save config: {e}")
        sys.exit(1)


def _find_claude_md() -> Optional[Path]:
    """Search upward from CWD for CLAUDE.md (git-style discovery)

    Returns:
        Path to CLAUDE.md if found, None otherwise
    """
    current = Path.cwd().resolve()
    root = Path(current.root)

    # Walk up directory tree until we find CLAUDE.md or hit root
    while current != root.parent:
        for name in ["CLAUDE.md", "AGENTS.md"]:
            candidate = current / name
            if candidate.exists() and candidate.is_file():
                return candidate

        # Move up one directory
        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent

    return None


def _add_to_claude_md(content: str) -> None:
    """Add AutoMem section to CLAUDE.md (or create if needed)"""
    claude_path = _find_claude_md()

    if claude_path:
        # Check if AutoMem section already exists
        existing_content = claude_path.read_text()
        if "## Memory Management with AutoMem" in existing_content:
            output_warning(f"{claude_path.name} already contains AutoMem section")
            output_info("If you want to update it, manually edit the file")
            return

        # Append the content
        with claude_path.open("a") as f:
            f.write("\n\n")
            f.write(content)
        output_success(f"Added AutoMem section to {claude_path}")
        output_info(f"File: {claude_path}")
    else:
        # Offer to create CLAUDE.md in current directory
        if click.confirm("CLAUDE.md not found. Create in current directory?", default=True):
            claude_path = Path.cwd() / "CLAUDE.md"
            claude_path.write_text(content + "\n")
            output_success(f"Created {claude_path}")
        else:
            output_info("Skipped. You can manually copy the content above to your documentation.")


@main.command()
@click.option("--no-prompt", is_flag=True, help="Skip interactive prompt to add to CLAUDE.md")
def onboard(no_prompt):
    """Generate agent integration instructions

    Outputs ready-to-paste documentation for CLAUDE.md or AGENTS.md.

    Example:
        am onboard >> CLAUDE.md
        am onboard --no-prompt  # Display only, no interactive prompt
    """
    # The actual content that goes into CLAUDE.md (without setup banner)
    claude_content = """
## Memory Management with AutoMem

**IMPORTANT**: Use **AutoMem CLI (`am`)** for ALL memory/context tracking.
Do NOT use markdown notes, comments, or TODO lists for persistent context.

### Why AutoMem?
- Semantic search: Find memories by meaning, not keywords
- Project isolation: Separate memory spaces per project
- Relationship tracking: Link related insights/discoveries
- Auto-consolidation: Decay old memories, strengthen patterns
- Session continuity: Restore context between work sessions

### Installation (if `am` tool unavailable)
```bash
uv tool install automem-cli
```

### Project Init (if `.automem/config.yml` not found)
```bash
# Run from project root (or any subdirectory)
am init --project my-project --endpoint http://localhost:8001 --token $AUTOMEM_API_TOKEN
```

The `am` tool searches upward from your current directory for `.automem/config.yml` (like git searches for `.git/`). If not found, it falls back to environment variables and defaults. Run `am init` once per project to create the config file.

### Agent Workflow

**Session Start (CRITICAL - run every session):**
```bash
am startup --json     # Load critical lessons, system rules, and recent context
```

This returns structured context in three categories:
- **Critical lessons**: High-importance memories tagged with `critical`, `lesson`, or `ai-assistant`
- **System rules**: Memories tagged with `system` or `memory-recall`
- **Recent memories**: 5 most recent memories for session continuation

Alternative (manual filtering):
```bash
am recall --importance-min 0.7 --json     # Load high-priority context
```

**During Work (spontaneous):**
```bash
# Store insights as you discover them
am store "Root cause: auth token expires too fast" -t insight -p 0.9 --json

# Link to what you were working on (with multiple tags)
am store "Fix: increase token TTL to 30min" -t decision -p 0.9 \\
    --tag authentication --tag security --tag bugfix \\
    --discovered-from <memory-id> --json

# Alternative: comma-separated tags
am store "Deploy to prod Friday 2pm" -t context -p 0.7 \\
    --tags deploy,production,scheduling --json

# Add contextual notes
am store "Bug fix for race condition" -t insight -p 0.9 \\
    --note "Affects user login flow during peak hours" --json

# Recall context when needed
am recall "authentication flow" --limit 5 --json
```

### Memory Types (case-insensitive)
- `decision`: Strategic choices and rationales (importance: 0.8-1.0)
- `insight`: Learned insights and discoveries (importance: 0.7-0.9)
- `pattern`: Recurring behaviors and approaches (importance: 0.6-0.8)
- `context`: Environmental and project context (importance: 0.4-0.6)
- `preference`: User preferences and settings (importance: 0.5-0.7)
- `style`: Coding/writing style patterns (importance: 0.5-0.7)
- `habit`: Regular practices and workflows (importance: 0.5-0.7)

### Importance Levels
- **0.9-1.0**: Critical (architectural decisions, major insights)
- **0.7-0.9**: High (important patterns, key learnings)
- **0.5-0.7**: Medium (useful context, preferences)
- **0.3-0.5**: Low (minor notes, might forget)
- **0.0-0.3**: Very low (archive candidate)

### Anti-Patterns (DO NOT)
- ❌ Markdown TODO lists for persistent context
- ❌ Code comments for architectural decisions
- ❌ Scattered notes across multiple files
- ❌ Forgetting to track discovery trails

### Best Practices (DO)
- ✅ Store insights immediately when discovered
- ✅ Use `--discovered-from` to track thought chains
- ✅ Use `--json` flag for programmatic use
- ✅ Set appropriate importance levels
- ✅ Add tags for categorization

### All Commands
```bash
am init                                           # Initialize workspace
am startup --json                                 # Load session context (critical/system/recent)
am store "content" [opts] --json                 # Store memory
  Options: -t type, -p priority, --tag (repeatable), --tags (CSV), --note, --discovered-from
am recall [query] --json                          # Recall memories
am health                                         # Check API connectivity
am consolidate                                    # Trigger consolidation
am onboard                                        # Generate this guide
am --help                                         # Full help
```

### Configuration

Environment variables (highest priority):
- `AUTOMEM_ENDPOINT` - API endpoint
- `AUTOMEM_API_TOKEN` - Authentication token
- `AUTOMEM_PROJECT_ID` - Project identifier

Or use `.automem/config.yml` in project root (created by `am init`).
"""

    # Setup banner to display before the main content
    setup_banner = """================================================================================
⚠️  REQUIRED SETUP - COMPLETE THESE STEPS NOW
================================================================================

**CRITICAL** (do immediately):
- [ ] **Add this entire section to CLAUDE.md (or AGENTS.md)**
      ↳ WHY: Without this, future AI sessions won't know AutoMem exists
      ↳ This is essential for session continuity and persistent memory
      ↳ Copy everything from "Memory Management with AutoMem" onwards

**Setup** (if not already done):
- [ ] Run `am init` in project root to create `.automem/config.yml`
- [ ] Set `AUTOMEM_API_TOKEN` environment variable
- [ ] Test with: `am store "test memory" -t context -p 0.5 --json`
- [ ] Remove any bootstrap/temporary memory instructions

================================================================================
"""

    # Display setup banner + content
    click.echo(setup_banner)
    click.echo(claude_content)

    # Interactive prompt to add to CLAUDE.md (unless --no-prompt)
    # Skip if output is piped (not a TTY), but allow in Click testing
    if not no_prompt:
        # Check if we're in an interactive context (TTY or Click test runner)
        is_interactive = sys.stdout.isatty() or (hasattr(sys.stdout, 'name') and sys.stdout.name == '<stdout>')

        # In CliRunner, stdin is a StringIO, so check for that too
        if is_interactive or 'click.testing' in sys.modules:
            click.echo("\n" + "=" * 80)
            if click.confirm("Would you like me to add this section to CLAUDE.md now?", default=True):
                _add_to_claude_md(claude_content)


@main.command()
@click.option("--mode", default="decay", help="Consolidation mode")
@click.option("--dry-run", is_flag=True, help="Preview without applying")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def consolidate(ctx, json_mode, mode, dry_run):
    """Trigger memory consolidation

    Modes: decay, creative, cluster, forget, full

    Example:
        am consolidate --mode decay

        am consolidate --mode full --dry-run --json
    """
    client = ctx.obj["client"]
    # Use local json_mode parameter, fallback to global if not provided
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    try:
        result = client.consolidate(mode=mode, dry_run=dry_run)

        if json_mode:
            output_json(result)
        else:
            output_success(f"Consolidation complete ({mode} mode)")
            if dry_run:
                output_info("(dry run - no changes applied)")
            output_rich("", result)

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code})
        else:
            output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.argument("memory1_id")
@click.argument("memory2_id")
@click.option("-t", "--type", "relation_type", default="RELATES_TO", help="Relationship type")
@click.option("-s", "--strength", type=float, default=0.5, help="Strength (0.0-1.0)")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def relate(ctx, json_mode, memory1_id, memory2_id, relation_type, strength):
    """Create relationship between memories

    Types: RELATES_TO, LEADS_TO, OCCURRED_BEFORE, REINFORCES, CONTRADICTS, etc.

    Example:
        am relate bd-42 bd-89 -t REINFORCES -s 0.9
    """
    client = ctx.obj["client"]
    # Use local json_mode parameter, fallback to global if not provided
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    try:
        result = client.create_association(
            memory1_id, memory2_id, relation_type=relation_type, strength=strength
        )

        if json_mode:
            output_json(result)
        else:
            output_success(f"Created {relation_type} relationship")
            output_info(f"{memory1_id} → {memory2_id}")

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code})
        else:
            output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def projects(ctx, json_mode):
    """List all projects with memory counts

    Example:
        am projects
        am projects --json
    """
    client = ctx.obj["client"]
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    try:
        result = client.list_projects()

        if json_mode:
            output_json(result)
        else:
            projects_list = result.get("projects", [])
            if not projects_list:
                output_info("No projects found")
                return

            output_success(f"Found {len(projects_list)} project(s):")
            for proj in projects_list:
                output_info(f"\n📁 {proj['id']}")
                output_info(f"   Memories: {proj['memory_count']}")
                output_info(f"   Patterns: {proj['pattern_count']}")
                has_qdrant = proj.get('has_qdrant_collection')
                if has_qdrant is True:
                    output_info(f"   Qdrant: ✓")
                elif has_qdrant is False:
                    output_info(f"   Qdrant: ✗")
                else:
                    output_info(f"   Qdrant: ?")

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code})
        else:
            output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.argument("project_id", required=False)
@click.option("--backup-dir", default="./backups", help="Backup directory")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def backup(ctx, project_id, backup_dir, json_mode):
    """Backup project data to compressed files

    Backs up FalkorDB graph and Qdrant vectors to timestamped files.
    If project_id provided, filters to that project only.

    Example:
        am backup thread --backup-dir ./my-backups
        am backup --json
    """
    import subprocess
    from pathlib import Path

    config = ctx.obj["config"]
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    # Use project_id from argument or fall back to config
    if not project_id:
        project_id = config.project_id

    try:
        # Build command for backup script
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "backup_automem.py"
        if not script_path.exists():
            # Try relative to cwd
            script_path = Path("scripts/backup_automem.py")
            if not script_path.exists():
                raise FileNotFoundError("Could not find backup_automem.py script")

        cmd = ["python3", str(script_path), "--backup-dir", backup_dir]

        if not json_mode:
            output_info(f"Backing up project '{project_id}' to {backup_dir}...")

        # Run backup script
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        if json_mode:
            # Parse JSON output from script
            import json
            try:
                backup_result = json.loads(result.stdout.strip().split('\n')[-1])
                output_json(backup_result)
            except json.JSONDecodeError:
                output_json({"status": "success", "output": result.stdout})
        else:
            output_success(f"Backup completed for project '{project_id}'")
            output_info(result.stdout)

    except subprocess.CalledProcessError as e:
        if json_mode:
            output_json({"error": str(e), "stderr": e.stderr})
        else:
            output_error(f"Backup failed: {e.stderr}")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.argument("falkordb_backup")
@click.option("--qdrant-backup", help="Qdrant backup file")
@click.option("--project-id", help="Filter to specific project")
@click.option("--dry-run", is_flag=True, help="Validate without restoring")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def restore(ctx, falkordb_backup, qdrant_backup, project_id, dry_run, json_mode):
    """Restore project data from backup files

    Example:
        am restore backups/falkordb/falkordb_20251104_121225.json.gz \\
                   --qdrant-backup backups/qdrant/qdrant_20251104_121225.json.gz \\
                   --project-id thread
        am restore --latest --dry-run
    """
    import subprocess
    from pathlib import Path

    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    try:
        # Build command for restore script
        script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "restore_automem.py"
        if not script_path.exists():
            # Try relative to cwd
            script_path = Path("scripts/restore_automem.py")
            if not script_path.exists():
                raise FileNotFoundError("Could not find restore_automem.py script")

        cmd = ["python3", str(script_path), "--falkordb-backup", falkordb_backup]

        if qdrant_backup:
            cmd.extend(["--qdrant-backup", qdrant_backup])
        if project_id:
            cmd.extend(["--filter-project", project_id])
        if dry_run:
            cmd.append("--dry-run")

        if not json_mode:
            output_info(f"Restoring from {falkordb_backup}...")
            if dry_run:
                output_info("(dry run mode - no changes will be made)")

        # Run restore script
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        if json_mode:
            # Parse JSON output from script
            import json
            try:
                # Script outputs JSON at the end between === lines
                lines = result.stdout.strip().split('\n')
                json_output = '\n'.join([l for l in lines if l and not l.startswith('=')])
                restore_result = json.loads(json_output.split('\n')[-1])
                output_json(restore_result)
            except json.JSONDecodeError:
                output_json({"status": "success", "output": result.stdout})
        else:
            output_success("Restore completed")
            output_info(result.stdout)

    except subprocess.CalledProcessError as e:
        if json_mode:
            output_json({"error": str(e), "stderr": e.stderr})
        else:
            output_error(f"Restore failed: {e.stderr}")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


@main.command()
@click.argument("project_id")
@click.option("--admin-token", envvar="ADMIN_API_TOKEN", help="Admin API token (or set ADMIN_API_TOKEN)")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.pass_context
def clear(ctx, project_id, admin_token, confirm, json_mode):
    """Clear all data for a project (DESTRUCTIVE)

    Deletes all Memory and Pattern nodes from FalkorDB and removes
    the project's Qdrant collection if it exists.

    Requires admin token for authorization.

    Example:
        am clear old-project --admin-token $ADMIN_API_TOKEN --confirm
    """
    client = ctx.obj["client"]
    if not json_mode:
        json_mode = ctx.obj["json_mode"]

    if not admin_token:
        output_error("Admin token required. Set ADMIN_API_TOKEN or use --admin-token")
        sys.exit(1)

    # Prompt for confirmation unless --confirm flag provided
    if not confirm and not json_mode:
        import click as click_module
        confirmed = click_module.confirm(
            f"⚠️  This will DELETE ALL data for project '{project_id}'. Continue?",
            default=False
        )
        if not confirmed:
            output_info("Cancelled")
            sys.exit(0)

    try:
        result = client.clear_project(project_id, admin_token)

        if json_mode:
            output_json(result)
        else:
            deleted = result.get("deleted", {})
            output_success(f"Cleared project '{project_id}'")
            output_info(f"Deleted {deleted.get('memories', 0)} memories")
            output_info(f"Deleted {deleted.get('patterns', 0)} patterns")
            if deleted.get('qdrant_collection'):
                output_info(f"Deleted Qdrant collection")

    except httpx.HTTPStatusError as e:
        if json_mode:
            output_json({"error": str(e), "status_code": e.response.status_code})
        else:
            output_error(f"HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        if json_mode:
            output_json({"error": str(e)})
        else:
            output_error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
