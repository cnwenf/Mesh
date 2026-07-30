"""``mesh completion`` — static shell completion (cli.md C22).

bash/zsh/fish scripts come from click's completion machinery (rendered once,
static); PowerShell gets a Register-ArgumentCompleter script built from the
live command tree (click has no built-in PowerShell backend).
"""

from __future__ import annotations

import click
from click.shell_completion import get_completion_class

from meshcli.main import cli as root


def _command_names() -> list[str]:
    return sorted(root.list_commands(ctx=None))


def _powershell_source() -> str:
    commands = " ".join(f"'{name}'" for name in _command_names())
    return f"""# mesh CLI PowerShell completion (static)
Register-ArgumentCompleter -Native -CommandName mesh -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $commands = @({commands})
    $commands | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }}
}}
"""


@root.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell"]))
def completion(shell):
    """Print a static completion script for the given shell.

    \b
    Install:
      mesh completion bash   >> ~/.bashrc
      mesh completion zsh    >> ~/.zshrc
      mesh completion fish   > ~/.config/fish/completions/mesh.fish
      mesh completion powershell >> $PROFILE
    """
    if shell == "powershell":
        click.echo(_powershell_source())
        return
    completion_class = get_completion_class(shell)
    if completion_class is None:  # pragma: no cover — click ships all three
        raise click.ClickException(f"no completion support for {shell}")
    instance = completion_class(root, {}, "mesh", "_mesh")
    click.echo(instance.source())
