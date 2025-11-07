"""Output formatting for CLI"""

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

console = Console()
error_console = Console(stderr=True)


def output_json(data: Any) -> None:
    """Output data as JSON to stdout

    Args:
        data: Any JSON-serializable data
    """
    try:
        print(json.dumps(data, indent=2))
    except (TypeError, ValueError) as e:
        sys.stderr.write(f"Error serializing to JSON: {e}\n")
        sys.exit(1)


def _extract_metadata(item: Any) -> Any:
    """Extract metadata from a memory item (handles nested structure)

    Args:
        item: Memory item (can be direct or nested under 'memory' key)

    Returns:
        Metadata dict or None
    """
    # Handle nested structure from recall API (has 'memory' key)
    if isinstance(item, dict) and "memory" in item:
        memory = item["memory"]
        if isinstance(memory, dict):
            return memory.get("metadata")

    # Handle flat structure (direct metadata field)
    if isinstance(item, dict):
        return item.get("metadata")

    return None


def _format_git_context(metadata: Any) -> str:
    """Format git context from metadata for display

    Args:
        metadata: Memory metadata (dict or None)

    Returns:
        Formatted git context string or empty string
    """
    if not metadata or not isinstance(metadata, dict):
        return ""

    git = metadata.get("git")
    if not git or not isinstance(git, dict):
        return ""

    branch = git.get("branch", "unknown")
    describe = git.get("describe", "")
    status = git.get("status", "clean")
    modified = git.get("modified_files", [])

    # Format: branch@commit (dirty: file1, file2) or (clean)
    parts = [branch]

    # Add describe/commit info if available
    if describe:
        # Extract short commit from describe (e.g., "v1.0.0-5-gabc1234-dirty" -> "abc1234")
        parts.append(f"@{describe}")

    if status == "dirty" and modified:
        files_str = ", ".join(modified[:3])  # Show first 3 files
        if len(modified) > 3:
            files_str += f", +{len(modified)-3} more"
        return f"{' '.join(parts)} ({files_str})"
    else:
        return f"{' '.join(parts)} ({status})"


def output_rich(message: str, data: Any = None) -> None:
    """Output rich formatted data to terminal

    Args:
        message: Message to display
        data: Optional data to format
    """
    console.print(f"[bold green]{message}[/bold green]")

    if data is None:
        return

    if isinstance(data, dict):
        # Display as key-value pairs
        for key, value in data.items():
            if key == "id":
                console.print(f"  [cyan]{key}[/cyan]: [yellow]{value}[/yellow]")
            elif key in ("importance", "score", "relevance"):
                # Format scores with color
                score = float(value)
                color = "green" if score > 0.7 else "yellow" if score > 0.4 else "red"
                console.print(f"  [cyan]{key}[/cyan]: [{color}]{score:.2f}[/{color}]")
            else:
                console.print(f"  [cyan]{key}[/cyan]: {value}")

    elif isinstance(data, list):
        if not data:
            console.print("  [dim](no items)[/dim]")
            return

        # Check if items are dicts with similar keys
        if all(isinstance(item, dict) for item in data):
            # For recall API results, extract the memory object if present
            display_items = []
            for item in data:
                if "memory" in item:
                    # Recall API result with nested memory object
                    memory = item["memory"]
                    # Add recall metadata to display
                    display_item = dict(memory)
                    display_item["score"] = item.get("final_score", item.get("match_score"))
                    display_items.append(display_item)
                else:
                    # Direct memory object
                    display_items.append(item)

            # Display as table with git context
            keys = set()
            for item in display_items:
                keys.update(item.keys())

            table = Table(show_header=True)
            for key in ["id", "content", "importance", "score"]:
                if key in keys:
                    table.add_column(key.capitalize())
                    keys.discard(key)

            # Add git column if any memory has git context
            has_git = any(_format_git_context(_extract_metadata(item)) for item in display_items)
            if has_git:
                table.add_column("Git Context")

            # Add remaining columns (excluding metadata-related fields for cleaner display)
            excluded_keys = {"metadata", "last_accessed", "updated_at", "tag_prefixes", "enrichment", "entities"}
            for key in sorted(keys - excluded_keys):
                if key not in {"id", "content", "importance", "score"}:
                    table.add_column(key.capitalize())

            for item in display_items:
                row = []
                for key in ["id", "content", "importance", "score"]:
                    if key in item:
                        value = item.get(key, "")
                        if key == "content" and len(str(value)) > 50:
                            value = str(value)[:47] + "..."
                        row.append(str(value))

                # Add git context if present
                if has_git:
                    metadata = _extract_metadata(item)
                    git_str = _format_git_context(metadata)
                    row.append(git_str)

                # Add remaining columns
                for key in sorted((keys - excluded_keys) - {"id", "content", "importance", "score"}):
                    row.append(str(item.get(key, "")))

                table.add_row(*row)

            console.print(table)
        else:
            # Display as list
            for i, item in enumerate(data, 1):
                console.print(f"  {i}. {item}")


def output_error(message: str) -> None:
    """Output error message to stderr

    Args:
        message: Error message
    """
    error_console.print(f"[bold red]Error:[/bold red] {message}")


def output_success(message: str) -> None:
    """Output success message

    Args:
        message: Success message
    """
    console.print(f"[bold green]✅[/bold green] {message}")


def output_warning(message: str) -> None:
    """Output warning message

    Args:
        message: Warning message
    """
    error_console.print(f"[bold yellow]⚠️[/bold yellow] {message}")


def output_info(message: str) -> None:
    """Output info message

    Args:
        message: Info message
    """
    console.print(f"[bold blue]ℹ️[/bold blue] {message}")
