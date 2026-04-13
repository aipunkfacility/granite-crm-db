# pipeline/status.py
from rich.console import Console
from rich.table import Table

__all__ = ["print_status", "print_table"]

# Eager initialization to avoid thread-race on lazy _get_console()
_console = Console()

def print_status(message: str, level: str = "info"):
    """Вывод статуса через rich."""
    console = _console
    if level == "info":
        console.print(f"[cyan]ℹ[/cyan] {message}")
    elif level == "success":
        console.print(f"[green]✔[/green] {message}")
    elif level == "warning":
        console.print(f"[yellow]⚠[/yellow] {message}")
    elif level == "error":
        console.print(f"[red]✖[/red] {message}")
    elif level == "bold":
        console.print(f"[bold white]{message}[/bold white]")
    else:
        console.print(f"[dim]{message}[/dim]  [red](unknown level: {level})[/red]")

def print_table(title: str, columns: list[str], rows: list[list[str]]):
    """Вывод красивой таблицы через rich."""
    console = _console
    table = Table(title=title, show_header=True, header_style="bold magenta")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)
