"""CLI interface facades."""

from app.interfaces.cli.commander import main as commander_main
from app.interfaces.cli.training import main as training_main
from app.interfaces.cli.web import main as web_main

__all__ = [
    "commander_main",
    "training_main",
    "web_main",
]
