"""Command-line control for Cync Bluetooth lights."""

__version__ = "0.1.0"


def main() -> int:
    """Run the cync command-line interface."""
    from .cli import main as cli_main

    return cli_main()


__all__ = ["__version__", "main"]
