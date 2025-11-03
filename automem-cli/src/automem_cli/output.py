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
            # Display as table
            keys = set()
            for item in data:
                keys.update(item.keys())

            table = Table(show_header=True)
            for key in ["id", "content", "importance", "score"]:
                if key in keys:
                    table.add_column(key.capitalize())
                    keys.discard(key)

            # Add remaining columns
            for key in sorted(keys):
                table.add_column(key.capitalize())

            for item in data:
                row = []
                for key in ["id", "content", "importance", "score"]:
                    if key in item:
                        value = item.get(key, "")
                        if key == "content" and len(str(value)) > 50:
                            value = str(value)[:47] + "..."
                        row.append(str(value))

                for key in sorted(keys):
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
