# poketerm

Linux collectable terminal Pokédex that captures sprites from the external tool `pokemon-colorscripts`.

This repository provides a simple workflow to "catch" Pokémon created from pokemon-colorscripts, keep a persistent pokédex, and view per-generation progress.

## Features

- Capture a random Pokémon sprite (normal or shiny) using `pokemon-colorscripts`.
- Persist caught Pokémon to a user pokédex file.
- Keep per-generation ordering using the generation lists in gen_files/.
- View a generation-specific catch progress report.

## Requirements

- pokemon-colorscripts (installed by the included installer if missing)
- hyfetch (installed by the included installer if missing)
- Zsh

## Installation

To install for the first time run:

1. Run the installer:

   ```/bin/bash
   sudo ./install.sh
   ```

This will:

- Copy generation lists to $HOME/.local/share/poketerm/gen_files
- Install or link the `pokedex` helper script to /usr/local/bin/pokedex
- Add the poketerm prompt script into your ~/.zshrc (backups ~/.zshrc to poketerm/zshrc.backup)

## Updating from 0.0.1 to 0.0.2

Updating poketerm will not lose any of your existing pokedex, to update poketerm simply pull from main and run the installer with the update flag:

1. Run the installer:

   ```/bin/bash
   git pull origin main
   sudo ./install.sh --update
   ```

This will:

- Format any updates to the $HOME/.local/share/poketerm/pokedex.txt
- Install or link the `pokedex` helper script to /usr/local/bin/pokedex
- Update the existing script in your ~/.zshrc

## Usage

- Run the capture script (installed as `/usr/local/bin/pokedex` by the installer) to view your stored pokédex per generation:
  pokedex [GEN_NUM 1-8]

  Example:
  - `pokedex` (defaults to generation 1)
  - `pokedex 3` (shows generation 3 progress)

- The capture behavior appended into your shell (see ~/.zshrc) hooks into `pokemon-colorscripts -r 1-8` to display a sprite and will:
  - Add the Pokémon name to the persistent pokedex file (if not already present).
  - Mark random 1-in-4096 encounters as shiny.

## Files of interest

- Installer: install.sh — sets up files and links the pokedex helper.
- Main helper: pokedex — display your per-generation progress and summary.
- Shell integration: zshrc — snippet that calls `pokemon-colorscripts`, updates pokedex, and displays the sprite via neofetch.
- Generation lists: gen_files/gen{1..8}_list.txt — canonical ordering used to sort your pokedex per generation.

## Notes

- The persistent pokedex is stored at $HOME/.local/share/poketerm/pokedex.txt (see the `POKEDEX_FILE` variable in the bundled script).
- Generated files and lists are installed to $HOME/.local/share/poketerm/.

## Contributing

- Feel free to open a PR with improvements to the scripts.

## To Do

- A more detailed Pokedex
