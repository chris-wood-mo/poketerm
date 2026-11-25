# poketerm

Collectable terminal Pokedex that captures sprites from the external tool `pokemon-colorscripts`.

This repository provides a simple service to "catch" Pokemon created from pokemon-colorscripts, keep a persistent pokedex, and view per-generation progress.

<https://github.com/user-attachments/assets/9277514e-d871-4d28-a3ae-59980b39fec3>

## Features

- Capture a random Pokemon sprite (normal or shiny) using `pokemon-colorscripts`.
- Persist caught Pokemon to a user pokédex file.
- Keep per-generation ordering using the generation lists in gen_files/.
- View a generation-specific catch progress report.
- Trade Pokemon with other trainers on your local network (press `t` in the Pokedex).

## Requirements

- macOS (Apple Silicon or Intel) or Linux (x86-64 or ARM)
- zsh
- curl (to download poketerm's own Python on install)
- Optional: [fastfetch](https://github.com/fastfetch-cli/fastfetch)

### poketerm's own Python

poketerm brings its own Python so it behaves the same on every machine, whatever version (if any) your system has. The installer downloads it once: Python 3.14 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), about 27-37 MB, into `~/.local/share/poketerm-python`. Trading's encryption package ([cryptography](https://cryptography.io)) comes with it. Every download is checked against a SHA256 hash pinned in `install.sh`, and anything that doesn't match is rejected. Updates only download it again when poketerm moves to a newer Python.

On a machine it isn't built for (e.g. 32-bit, or Alpine Linux), or if the download fails, poketerm uses your system's `python3` instead. Trading then also needs Python 3 with TLS 1.3 and the `openssl` command.

## Installation

```bash
git clone https://github.com/chris-wood-mo/poketerm.git
cd poketerm
./install.sh
source ~/.zshrc
```

For specific generations, default is 1-8:

```bash 
git clone https://github.com/chris-wood-mo/poketerm.git
cd poketerm
./install.sh --gen 1
source ~/.zshrc
```

```bash
git clone https://github.com/chris-wood-mo/poketerm.git
cd poketerm
./install.sh --gen 2-5
source ~/.zshrc
```

## Update

```bash
poketerm --update
```

That's it. Your Pokedex and trainer progress are kept (and backed up to `~/.local/share/poketerm/backups` before every update), your `--gen` setting is kept, and the new version is used from the next terminal you open. If an update fails part-way, the previous version is restored automatically.

Poketerm checks for a new version once a day in the background and shows a one-line notice when one is available. Set `POKETERM_NO_UPDATE_CHECK=1` to turn this off.

```bash
poketerm --update --check     # just check whether an update is available
poketerm --update 0.0.7       # install a specific version (add --force to go back to an older one)
poketerm --update --beta      # try pre-release versions early
```

To change which generations you catch, re-run the installer with `--gen`:

```bash
cd poketerm
./install.sh --update --gen 2-5
```

> Upgrading from 0.0.6 or earlier? Those versions don't have `poketerm --update` yet, so update once the old way: `cd poketerm && git pull && ./install.sh --update` (no `sudo` needed any more). Your Pokedex, trainer profile, XP and achievements are kept, and your old `~/.zshrc` block is upgraded automatically. After that, `poketerm --update` works.

## Troubleshooting

**Powerlevel10k shows "Console output during zsh initialization detected"**

Poketerm prints to the terminal when it starts, so with Powerlevel10k's instant prompt its block in `~/.zshrc` must come *before* the instant-prompt block. The installer puts it there automatically - just update (`poketerm --update`, or `./install.sh --update` on 0.0.6 and earlier). To fix it by hand, move everything between `# >>> POKETERM SCRIPT BEGIN >>>` and `# <<< POKETERM SCRIPT END <<<` above the line `# Enable Powerlevel10k instant prompt`.

## Development and releasing

```bash
./install.sh --dev                # link your install to this checkout: edits apply to the next terminal you open
poketerm --update --from .        # install this checkout as a normal (copied) build, e.g. to test before release
./release.sh 0.0.8-beta.1         # publish a pre-release - only `poketerm --update --beta` users get it
./release.sh 0.0.8                # publish a stable release from main - all users are offered it
./release.sh 0.0.8 --dry-run      # run every check (syntax + the full test suite) without publishing
./test.sh                         # run the end-to-end tests in a throwaway HOME (-v for details)
```

`release.sh` checks the version is newer than the last release, the tree is clean, and a fresh install catches a Pokemon and earns XP, then bumps `VERSION`, tags, pushes and creates the GitHub release (needs the [GitHub CLI](https://cli.github.com/)). The version lives only in the `VERSION` file.

## Remove

```bash
cd poketerm
./install.sh --uninstall
```

This removes the Poketerm install you will need to remove the zshrc entry manually.

## Usage

```bash
# Open the default Pokedex view
pokedex

# Open a specific generation in the Pokedex
pokedex 3

# Show the Pokedex help
pokedex --help

# Run Poketerm manually (default generation range)
poketerm

# Run a specific generation or range manually
poketerm --gen 1
poketerm --gen 2-5

# Show the Poketerm help
poketerm --help

# Show the current version
poketerm --version

# Update to the latest version
poketerm --update

# Remove a manually added Pokemon from the Pokedex
poketerm --remove pikachu
```

When the shell starts, Poketerm will auto-run and keep catching Pokemon in your terminal.

## Trading

Swap Pokemon with a friend on the same network.

1. You both open the Pokedex and press `t`.
2. Pick the Pokemon you want to give and press `Enter`, then pick your friend from **Trainers nearby** and press `Enter`.
3. Your friend sees your invite. **Check the 6-digit pairing code is the same on both screens**, then they press `y`.
4. Your friend picks the Pokemon they'll give you. When you're both happy, you each press `y` to confirm.
5. Your Pokemon is sent off in its Poke Ball, and the one you receive pops out of a Poke Ball.

Good to know:

- You can make **5 trades a day**. The trade page shows how many you have left.
- You give one of the Pokemon you've caught. If it was your last one, it leaves your Pokedex. Shiny Pokemon stay shiny.
- Only trainers with their trade page open can be seen or contacted. Close it and you're invisible.
- If your friend isn't listed, press `c` and type the address shown on their trade page.
- **On a Mac, the first time you trade, macOS may ask whether Python may accept incoming connections.** Choose **Allow**, or nobody can invite you.
- If the connection drops at the very last moment, nothing is lost. The trade finishes automatically the next time you're both on the trade page.

## Achievements

The list of achievements you can obtain are:

**Pokedex Milestones**

- First Pokemon Caught
- 50 Pokemon Caught
- 100 Pokemon Caught
- 905 Pokemon Caught
- HIDDEN
- Complete Generation 1
- Complete Generation 2
- Complete Generation 3
- Complete Generation 4
- Complete Generation 5
- Complete Generation 6
- Complete Generation 7
- Complete Generation 8
- HIDDEN

**Catch Achievements**

- 3 Day Catch Streak
- 7 Day Catch Streak
- 30 Day Catch Streak
- 100 Day Catch Streak
- 365 Day Catch Streak
- HIDDEN
- Caught 5 Shiny Pokemon
- Caught 100 Shiny Pokemon
- Caught 5 Pokemon in a day
- Caught 10 Pokemon in a day
- HIDDEN
- Caught a Pokemon 10 times
- Caught a Pokemon 25 times

**Level Milestones**

- Reach level 10
- Reach level 25
- HIDDEN
- Reach a total XP of 1000
- Reach a total XP of 10000
- Reach a total XP of 50000
- HIDDEN

**Special Collections**

- HIDDEN
- HIDDEN
- HIDDEN
- HIDDEN
- HIDDEN
- HIDDEN
- HIDDEN
- HIDDEN

There are 42 achievements in total for you to unlock. Some have been left hidden purposly so you can unlock them as you continue collecting!

## Credits

- All the pokemon designs, names, branding etc. are trademarks of [The Pokemon Company](https://www.pokemon.com/uk)
- [Pokemon-Colorscripts](https://gitlab.com/phoneybadger/pokemon-colorscripts) for the sprites
- [pokemon-go-colorscripts](https://github.com/scottmckendry/pokemon-go-colorscripts) (MIT) - a Go build of the same sprites that poketerm uses when available for faster startup. The installer pins a reviewed release and verifies its SHA256 checksum, falling back to the Python version otherwise.
- [PokeAPI](https://pokeapi.co/) for the data around the pokemon

## Contributing

- Feel free to fork the repo and open a PR with improvements to the scripts.

## To Do

- A search feature for the pokedex
- Add Gen 9 to the pokedex, open PR needs to be merged with pokemon-colorscripts
