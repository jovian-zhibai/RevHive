"""CLI entry point for CodeGuardian."""

import asyncio
import logging
import sys
from pathlib import Path

import click

from codeguardian import __version__
from codeguardian.config import load_config
from codeguardian.graph.workflow import CodeReviewWorkflow, ReviewReport

logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version=__version__)
def cli():
    """CodeGuardian - AI-powered multi-agent code review system."""
    pass


def _run_with_timeout(coro, timeout: int = 300):
    """Run an async coroutine with a timeout (default 5 minutes)."""
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
    except asyncio.TimeoutError:
        click.echo("Error: Review timed out after 5 minutes.", err=True)
        sys.exit(1)


@cli.command()
@click.option("--file", "-f", type=click.Path(exists=True), help="Path to file for review")
@click.option("--diff", "-d", "diff_ref", help="Git diff reference (e.g., HEAD~1)")
@click.option("--model", "-m", default=None, help="LLM model to use")
@click.option("--output", "-o", type=click.Path(), help="Output file path")
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]), default="markdown")
def review(file: str, diff_ref: str, model: str, output: str, fmt: str):
    """Run code review on a file or git diff."""
    cfg = load_config()
    workflow = CodeReviewWorkflow(model=model, config=cfg)

    if file:
        if cfg.should_ignore(file):
            click.echo(f"Skipping {file} — matches ignore pattern in .codeguardian.yml")
            return
        try:
            code = Path(file).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            click.echo(f"Error: {file} is not a valid text file (encoding issue).", err=True)
            sys.exit(1)
        except (PermissionError, OSError) as exc:
            click.echo(f"Error: cannot read {file}: {exc}", err=True)
            sys.exit(1)
        result = _run_with_timeout(workflow.run(code=code, file_path=file))
    elif diff_ref:
        result = _run_with_timeout(workflow.run_from_diff(diff_ref))
    else:
        click.echo("Please specify --file or --diff")
        return

    report_obj = ReviewReport(result)
    if fmt == "json":
        report = report_obj.to_json()
    else:
        report = report_obj.to_markdown()

    if output:
        Path(output).write_text(report, encoding="utf-8")
        click.echo(f"Report saved to {output}")
    else:
        click.echo(report)


if __name__ == "__main__":
    cli()
