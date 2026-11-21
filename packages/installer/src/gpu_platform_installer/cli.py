from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .checks import CompatibilityError
from .config import load_config
from .lifecycle import Installer

app = typer.Typer(no_args_is_help=True, help="Validate and deploy a configured compute cluster.")


def installer(config: Path, host: str, output: Optional[Path]) -> Installer:
    parsed = load_config(config)
    return Installer(parsed, host, output or Path(parsed.cluster.state_directory) / "deploy" / host)


def execute(action):
    try:
        result = action()
    except (ValueError, RuntimeError, OSError) as error:
        if isinstance(error, CompatibilityError):
            typer.echo(json.dumps({"errors": [{"code": item.code, "parameters": item.parameters} for item in error.issues]}), err=True)
        else:
            typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    if result is not None:
        typer.echo(json.dumps(result, indent=2, default=str))


@app.command()
def validate(config: Path = typer.Option(Path("config/platform.default.yaml"), "--config", "-c"),
             host: str = typer.Option(..., "--host"), machine: bool = typer.Option(False, "--machine")):
    """Check modules, handoffs, feature dependencies, and optional machine requirements."""
    execute(lambda: installer(config, host, None).plan(machine_checks=machine))


@app.command()
def plan(config: Path = typer.Option(Path("config/platform.default.yaml"), "--config", "-c"),
         host: str = typer.Option(..., "--host"), output: Optional[Path] = typer.Option(None, "--output")):
    """Print installation order and the generated Compose document."""
    execute(lambda: installer(config, host, output).plan())


@app.command()
def install(config: Path = typer.Option(Path("config/platform.default.yaml"), "--config", "-c"),
            host: str = typer.Option(..., "--host"), output: Optional[Path] = typer.Option(None, "--output")):
    """Check the host, install module prerequisites, and start containers."""
    execute(lambda: installer(config, host, output).install())


@app.command()
def upgrade(config: Path = typer.Option(Path("config/platform.default.yaml"), "--config", "-c"),
            host: str = typer.Option(..., "--host"), output: Optional[Path] = typer.Option(None, "--output"),
            maintenance_reports: Optional[Path] = typer.Option(None, "--maintenance-reports")):
    """Upgrade after every node enters maintenance and finishes its tasks."""
    instance = installer(config, host, output)
    instance.maintenance_reports = maintenance_reports
    execute(instance.upgrade)


@app.command("maintenance-report")
def maintenance_report(config: Path = typer.Option(Path("config/platform.default.yaml"), "--config", "-c"),
                       host: str = typer.Option(..., "--host"), output: Path = typer.Option(..., "--output")):
    """Export fresh drained-node reports on the control host for a worker upgrade."""
    execute(lambda: installer(config, host, None).export_maintenance_reports(output))


@app.command()
def uninstall(config: Path = typer.Option(Path("config/platform.default.yaml"), "--config", "-c"),
              host: str = typer.Option(..., "--host"), output: Optional[Path] = typer.Option(None, "--output"),
              maintenance_reports: Optional[Path] = typer.Option(None, "--maintenance-reports")):
    """Stop containers and uninstall modules in reverse order, retaining persisted data."""
    instance = installer(config, host, output)
    instance.maintenance_reports = maintenance_reports
    execute(instance.uninstall)


if __name__ == "__main__":
    app()
