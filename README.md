# poketerm

Linux collectable terminal Pokédex that captures sprites from the external tool `pokemon-colorscripts`.

This repository provides a simple workflow to "catch" Pokémon created from pokemon-colorscripts, keep a persistent pokédex, and view per-generation progress.

## Features

- Capture a random Pokémon sprite (normal or shiny) using `pokemon-colorscripts`.
- Persist caught Pokémon to a user pokédex file.
- Keep per-generation ordering using the generation lists in gen_files/.
- View a generation-specific catch progress report.

## Requirements

- Homebrew
- Python3
- Zsh
- readchar (Can be installed via `pip3 install -r requirements.txt`)
- pokemon-colorscripts (installed by the included installer via Homebrew if missing)
- hyfetch (installed by the included installer via Homebrew if missing)

## Installation

To install for the first time run:

1. Run the installer:

   ```/bin/bash
   sudo ./install.sh
   ```

   Or for installing and using just a specific generation, default is 1-8: 
   ```
   ```/bin/bash
   sudo ./install.sh --gen 1 or sudo ./install.sh --gen 2-5
   ```

This will:

- Copy generation lists to $HOME/.local/share/poketerm/gen_files
- Copy cache api and sprite data to $HOME/.local/share/poketerm/cache
- Install or link the `pokedex` helper script to /usr/local/bin/pokedex
- Install the poketerm shell helper
- Add the poketerm prompt script into your ~/.zshrc (backups ~/.zshrc to poketerm/zshrc.backup)

## Updating

Updating poketerm will not lose any of your existing pokedex. Pull changes and run the installer with the update flag:

1. Run the installer:

   ```/bin/bash
   git pull origin main
   sudo ./install.sh --update
   ```

This will:

- Run any necessary migrations from the bundled migrations/ directory to bring your pokedex and generated files up to date.
- Format any updates to $HOME/.local/share/poketerm/pokedex.txt
- Reinstall or relink the `pokedex` helper script to /usr/local/bin/pokedex
- Update the poketerm snippet in your ~/.zshrc so it calls the installed poketerm helper

Current Update Paths:

- 0.0.1 -> 0.0.2
- 0.0.2 -> 0.0.3
- 0.0.3 -> 0.0.4: Note after this update to use a specific generation of pokemon you will need to update your zshrc from poketerm -> poketerm --gen 1 or poketerm --gen 2-5.
- 0.0.4 -> 0.0.5: Note you may need to install the readchar python package, this can be done via `pip3 install -r requirements.txt`.

## Usage

- Run the capture script (installed as `/usr/local/bin/pokedex` by the installer) to view your stored pokédex per generation:
  pokedex [GEN_NUM 1-8]

  Example:
  - `pokedex` (defaults to generation 1)
  - `pokedex 3` (shows generation 3 progress)
  - `pokedex -h/--help/help` (shows help)

- The capture behavior appended into your shell (see ~/.zshrc) hooks into `pokemon-colorscripts -r 1-8` to display a sprite and will:
  - Add the Pokémon name to the persistent pokedex file (if not already present).
  - Mark random 1-in-4096 encounters as shiny.

## Files of interest

- Installer: install.sh — sets up files and links the pokedex helper.
- Main helper: pokedex — display your per-generation progress and summary.
- Shell integration: zshrc — snippet that calls `pokemon-colorscripts`, updates pokedex, and displays the sprite via neofetch.
- Generation lists: gen_files/gen{1..8}_list.txt — canonical ordering used to sort your pokedex per generation.
- Cached data: Caches sprites from `pokemon-colorscripts` and pokemon data from pokeapi for faster lookup

## Uninstalling

To uninstall poketerm please run the following command:

1. Run the installer:

   ```/bin/bash
   sudo ./install.sh --uninstall
   ```

This will:

- Uninstall Hyfetch
- Uninstall pokemon-colorscripts
- Uninstall poketerm

## Notes

- The persistent pokedex is stored at $HOME/.local/share/poketerm/pokedex.txt (see the `POKEDEX_FILE` variable in the bundled script).
- The installer will apply migrations and take steps to protect the pokedex file (make it harder to accidentally or trivially edit)
- Generated files and lists are installed to $HOME/.local/share/poketerm/.
- Cached data is installed to $HOME/.local/share/poketerm/.

## Credits

- All the pokemon designs, names, branding etc. are trademarks of [The Pokémon Company](https://www.pokemon.com/uk)
- [Pokemon-Colorscripts](https://gitlab.com/phoneybadger/pokemon-colorscripts) for the sprites
- [PokeAPI](https://pokeapi.co/) for the data around the pokemon

## Contributing

- Feel free to open a PR with improvements to the scripts.

## To Do

- A search feature for the pokedex
- Add Gen 9 to the pokedex
