# My contributions:
- impl: implemented the terminal CLI entry point using Typer, including configuration, prompt, and workspace command-line options.
- impl: implemented YAML configuration loading with clear handling for missing files, invalid YAML, and invalid configuration structures.
- impl: added validation for the `INNKUBE_ACCESS_TOKEN` environment variable while allowing the current CLI to run in standalone/offline mode.
- impl: implemented the interactive REPL with empty-input handling, exit commands, keyboard-interrupt handling, and readable error messages.
- impl: created reusable Rich display helpers for the welcome banner, agent responses, tool calls, system messages, errors, and the processing spinner.
- impl: updated the project dependencies to include Rich and Typer's optional CLI features.
- test: added unit tests for the display helpers, REPL behaviour, configuration loading, environment-token validation, and Typer CLI execution.
- review: reviewed how the CLI modules work together and documented that the current one-shot and interactive responses are placeholders until the ReAct loop is connected.

# Team:
- Had an agreement to discuss the project architecture.
- Kept the CLI implementation isolated on the `feature/cli-interface` branch instead of committing directly to `main`.
- Pushed commit `6e4263e` (`Implement CLI interface`) to the remote feature branch so teammates can review it before merging.
- Preserved the environment-variable approach for the InnKube access token so no secret is committed to GitLab.

