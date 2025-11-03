# AutoMem CLI

Agent-first command-line interface for AutoMem memory management.

## Installation

```bash
# Recommended: Install with uv
uv tool install automem-cli

# Verify installation
am --version
```

## Quick Start

```bash
# Initialize workspace
am init

# Generate agent instructions
am onboard >> CLAUDE.md

# Store a memory
am store "User prefers dark mode" -t preference -p 0.8 --json

# Recall memories
am recall "user preferences" --json

# Create relationships
am relate <memory-id-1> <memory-id-2> -t REINFORCES
```

## Configuration

### Environment Variables (Recommended)

```bash
export AUTOMEM_ENDPOINT="http://localhost:8001"
export AUTOMEM_API_TOKEN="your-token-here"
export AUTOMEM_PROJECT_ID="your-project"
```

### Configuration File

Alternatively, use `.automem/config.yml` in your project root:

```yaml
endpoint: http://localhost:8001
api_token: ${AUTOMEM_API_TOKEN}  # Or actual token
project_id: default
auto_consolidate: true
session_tracking: true
```

Create with: `am init`

## Commands

### Store Memory

```bash
am store "content" [OPTIONS]

Options:
  -t, --type TEXT              Memory type (decision, insight, pattern, context, preference)
  -p, --priority FLOAT         Importance/priority (0.0-1.0)
  --tag TEXT                   Tags (repeatable)
  --discovered-from TEXT       Memory ID this was discovered from
  --json                       Output JSON
```

Examples:
```bash
# Store with type and priority
am store "API redesign decision" -t decision -p 0.9 --json

# Track discovery trail
am store "Found race condition in auth" -t insight -p 0.9 \
  --discovered-from bd-42 --json

# Add tags
am store "User feedback on UI" -t context -p 0.6 \
  --tag ux --tag feedback --json
```

### Recall Memories

```bash
am recall [QUERY] [OPTIONS]

Options:
  -l, --limit INT              Maximum results (default: 10)
  --importance-min FLOAT       Minimum importance filter
  --importance-max FLOAT       Maximum importance filter
  --tag TEXT                   Filter by tags (repeatable)
  -t, --type TEXT              Filter by type
  --json                       Output JSON
```

Examples:
```bash
# Semantic search
am recall "authentication flow" --json

# Filter by importance
am recall --importance-min 0.8 --limit 5 --json

# Filter by type and tags
am recall --type decision --tag api --json

# Get all high-priority memories
am recall --importance-min 0.7 --json
```

### Create Relationships

```bash
am relate MEMORY1_ID MEMORY2_ID [OPTIONS]

Options:
  -t, --type TEXT              Relationship type (default: RELATES_TO)
  -s, --strength FLOAT         Strength (0.0-1.0, default: 0.5)
  --json                       Output JSON
```

Relationship types:
- `RELATES_TO` - General relationship
- `LEADS_TO` - Causal relationship
- `OCCURRED_BEFORE` - Temporal relationship
- `REINFORCES` - Strengthens pattern
- `CONTRADICTS` - Conflicting information
- `DISCOVERED_FROM` - Discovery trail

Examples:
```bash
# Create general relationship
am relate bd-42 bd-89 --json

# Create specific relationship type
am relate bd-42 bd-89 -t REINFORCES -s 0.9 --json
```

### Consolidation

```bash
am consolidate [OPTIONS]

Options:
  --mode TEXT                  Consolidation mode (decay, creative, cluster, forget, full)
  --dry-run                    Preview without applying
  --json                       Output JSON
```

Consolidation modes:
- `decay` - Update relevance scores based on time/importance
- `creative` - Discover non-obvious connections
- `cluster` - Group similar memories
- `forget` - Archive low-relevance memories
- `full` - Run all consolidation modes

Examples:
```bash
# Run decay consolidation
am consolidate --mode decay --json

# Preview full consolidation
am consolidate --mode full --dry-run --json
```

### Workspace Setup

```bash
# Initialize workspace
am init

# Generate agent integration docs
am onboard

# Check help
am --help
am store --help
```

## JSON Output

All commands support `--json` flag for programmatic use:

```bash
# Store and capture ID
MEMORY_ID=$(am store "test" --json | jq -r '.id')

# Recall and filter
am recall "auth" --json | jq '.[] | select(.importance > 0.8)'

# Chain commands
am recall --importance-min 0.9 --json | \
  jq -r '.[].id' | \
  xargs -I {} am relate {} bd-42 --json
```

## Agent Workflow

### Recommended Pattern

**Start of work session:**
```bash
# Load high-priority context
am recall --importance-min 0.7 --limit 10 --json
```

**During work (spontaneous):**
```bash
# Store insights immediately
am store "Discovery: token expiry causes issue" -t insight -p 0.9 \
  --discovered-from <working-on-memory-id> --json

# Recall when needed
am recall "authentication" --json
```

**End of session:**
```bash
# Store session summary
am store "Fixed auth token expiry issue" -t decision -p 0.9 --json

# Optional: Run consolidation
am consolidate --mode decay --json
```

## Development

### Local Installation

```bash
# Clone repository
git clone <repo-url>
cd automem-cli

# Install with uv (development mode)
uv tool install -e .

# Run tests
uv run pytest

# Format code
uv run ruff format .

# Lint
uv run ruff check .
```

### Project Structure

```
automem-cli/
├── pyproject.toml          # Package configuration
├── README.md               # This file
├── src/
│   └── automem_cli/
│       ├── __init__.py     # Package init
│       ├── __main__.py     # Entry point
│       ├── cli.py          # CLI commands
│       ├── api.py          # HTTP client
│       ├── config.py       # Configuration
│       └── output.py       # Output formatting
└── tests/
    └── test_cli.py         # Tests
```

## Troubleshooting

### Connection Refused

```bash
# Check if AutoMem server is running
curl http://localhost:8001/health

# Verify endpoint configuration
am recall --endpoint http://localhost:8001 --json
```

### Authentication Failed

```bash
# Check token is set
echo $AUTOMEM_API_TOKEN

# Test with explicit token
am recall --token your-token --json
```

### Project Isolation

```bash
# Check current project
am recall --json | jq -r '.[0].project_id'

# Use different project
am store "test" --project other-project --json
```

## License

See main AutoMem repository for license information.
