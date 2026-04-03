"""
Run once: python setup.py
Sets up everything automatically.
"""

import os
import subprocess
import sys
from pathlib import Path

REQUIRED_PACKAGES = [
    "requests",
    "beautifulsoup4",
    "apscheduler",
    "python-telegram-bot==20.7",
    "pyyaml",
    "python-dotenv",
    "lxml",
    "Pillow",
    "flask",
    "rich",
    "numpy",
]

FOLDER_STRUCTURE = [
    "data/snapshots",
    "config",
    "monitor",
    "dashboard",
]

DEFAULT_ENV = """TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_RECIPIENTS=you@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
"""

def setup():
    from rich.console import Console
    from rich.progress import track
    console = Console()

    console.print("\n[bold cyan]WebMonitor Setup[/bold cyan]\n")

    # Create folders
    console.print("[yellow]Creating folder structure...[/yellow]")
    for folder in FOLDER_STRUCTURE:
        Path(folder).mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/green] {folder}")

    # Install packages
    console.print("\n[yellow]Installing packages...[/yellow]")
    for package in track(REQUIRED_PACKAGES, description="Installing..."):
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package, "-q"]
        )

    # Install Playwright browser
    console.print("\n[yellow]Installing Playwright browser...[/yellow]")
    subprocess.run(["playwright", "install", "chromium"])

    # Create .env template
    if not Path(".env").exists():
        with open(".env", "w") as f:
            f.write(DEFAULT_ENV)
        console.print("\n[yellow]Created .env file[/yellow]")

    console.print("\n[bold green]✓ Setup complete![/bold green]")
    console.print("\nNext steps:")
    console.print("  1. Edit [cyan].env[/cyan] with your Telegram/Email details")
    console.print("  2. Edit [cyan]config/sites.yaml[/cyan] with your sites")
    console.print("  3. Run [cyan]python run.py start[/cyan]\n")


if __name__ == "__main__":
    subprocess.check_call([sys.executable, "-m", "pip", "install", "rich", "-q"])
    setup()
