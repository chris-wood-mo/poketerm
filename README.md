# poketerm

Collectable terminal Pokedex that captures sprites from the external tool `pokemon-colorscripts`.

This repository provides a simple service to "catch" Pokemon created from pokemon-colorscripts, keep a persistent pokedex, and view per-generation progress.

<https://github.com/user-attachments/assets/9277514e-d871-4d28-a3ae-59980b39fec3>

## Features

- Capture a random Pokemon sprite (normal or shiny) using `pokemon-colorscripts`.
- Persist caught Pokemon to a user pokédex file.
- Keep per-generation ordering using the generation lists in gen_files/.
- View a generation-specific catch progress report.
- Every Pokedex page is shown on a handheld Pokedex drawn in your terminal: a screen for the page, a side panel with details for whatever's selected, and buttons showing the keys. In smaller windows it switches to a compact layout automatically.
- Trade Pokemon with other trainers on your local network (press `t` in the Pokedex).
- Every Pokemon you catch has its own story: when it was caught, its nature, which number catch it was, and every trainer it has belonged to.

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

That's it. Your Pokedex and trainer progress are kept (and backed up to `~/.local/share/poketerm/backups` before every update), your settings are kept, and the new version is used from the next terminal you open. If an update fails part-way, the previous version is restored automatically.

Poketerm checks for a new version once a day in the background and shows a one-line notice when one is available. Turn this off on the Pokedex's settings page (`pokedex`, then `s`), or set `POKETERM_NO_UPDATE_CHECK=1`.

```bash
poketerm --update --check     # just check whether an update is available
poketerm --update 0.0.7       # install a specific version (add --force to go back to an older one)
poketerm --update --beta      # try pre-release versions early
```

To change which generations you catch, use the [settings page](#settings) (`pokedex`, then `s`).

> Upgrading from 0.0.6 or earlier? Those versions don't have `poketerm --update` yet, so update once the old way: `cd poketerm && git pull && ./install.sh --update` (no `sudo` needed any more). Your Pokedex, trainer profile, XP and achievements are kept, and your old `~/.zshrc` block is upgraded automatically. After that, `poketerm --update` works.

## Moving to a new computer

Take everything with you (your Pokedex, trainer profile, XP and achievements, every Pokemon's story and nickname, your settings and your trading identity) in one file:

```bash
# On your old computer
poketerm --export                 # saves poketerm-<name>-<date>.poketerm here (or: poketerm --export FILE)

# On your new computer: install poketerm, copy the file over, then
poketerm --import poketerm-ash-2026-11-25.poketerm
```

- **It's protected by a passphrase** you choose when exporting (at least 8 characters). You'll need it to import. The file holds your trading identity's private key, so keep it private.
- **Importing checks everything before changing anything**: a wrong passphrase, or a damaged or edited file, is refused and nothing on the new computer changes.
- **It replaces the new computer's progress.** It shows you what's in the file and what's already there, and asks first (add `--yes` to skip the question). The new computer's previous progress is backed up to `~/.local/share/poketerm/backups`.
- **Other trainers still know you.** Your trading identity comes with you, along with your trade history and today's trade count. Once you've moved, trade from the new computer only.
- Finish any trade that's still in progress before exporting. poketerm will tell you if one is.

## Troubleshooting

**Powerlevel10k shows "Console output during zsh initialization detected"**

Poketerm prints to the terminal when it starts, so with Powerlevel10k's instant prompt its block in `~/.zshrc` must come *before* the instant-prompt block. The installer puts it there automatically - just update (`poketerm --update`, or `./install.sh --update` on 0.0.6 and earlier). To fix it by hand, move everything between `# >>> POKETERM SCRIPT BEGIN >>>` and `# <<< POKETERM SCRIPT END <<<` above the line `# Enable Powerlevel10k instant prompt`.

## Development and releasing

```bash
poketerm --update --from .        # install this checkout as a normal (copied) build, e.g. to test before release
./release.sh 1.0.0-beta.1         # publish a pre-release - only `poketerm --update --beta` users get it
./release.sh 1.0.0                # publish a stable release from main - all users are offered it
./release.sh 1.0.0 --dry-run      # run every check (syntax + the full test suite) without publishing
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

### Pokedex keys

| Key | Does |
|---|---|
| `↑` `↓` | Move through the list |
| `←` `→` | Previous / next page |
| `Enter` | Open the Pokemon's entry |
| `/` | **Search** by name or number, across all generations: type `pika`, `mr mime` or `150`, then `Enter` to jump there (`Esc` to cancel) |
| `p` | Trainer profile |
| `h` | [Catch history](#catch-history) |
| `a` | Achievements |
| `t` | Trading |
| `s` | Settings |
| `q` | Quit |

## Catch history

Press `h` in the Pokedex to see your catching habits:

- **First and latest catch**, with the date and time
- **Rarest catch**: shinies first, then Mythical, Legendary and pseudo-legendary Pokemon, then any you've only caught once
- **Busiest day**: the day you caught the most
- **When you catch**: your catches split into morning, afternoon, evening and night, and what that makes you, e.g. *You're a night-time catcher!*

Every Pokemon you catch counts, even ones you've since traded away. Pokemon you received in trades don't count, because someone else caught them. Pokemon caught before poketerm 1.0 are included in the total, but their catch times weren't recorded, so they aren't part of the rest.

## Settings

Open the Pokedex (`pokedex`) and press `s`. Move with `↑`/`↓` and press `Enter` (or space) to change a setting; it's saved straight away and applies from the next terminal you open.

| Setting | Does |
|---|---|
| **Generations** (Gen 1-8) | Which generations new terminals catch from. At least one has to stay on; `a` turns them all back on |
| **System details** | Show or hide the system information (OS, host, …) beside the Pokemon when a terminal opens |
| **Trade animations** | Play the Poke Ball animation when a trade completes, or skip straight to the result |
| **Daily update check** | Check once a day for a new version of poketerm |

The bottom of the settings page shows which version of poketerm you have (so does `poketerm --version`).

Settings are kept in `~/.local/share/poketerm/settings.conf` and survive updates. `./install.sh --gen 2-5` sets the generations too, and `poketerm --gen 3` catches from Gen 3 for just that one run. If you set generations with `--gen` in an older version, they're moved into the settings automatically when you update.

## Your Pokemon's stories

Every Pokemon you catch is remembered individually. Open one in the Pokedex (`Enter` on it) and use `←`/`→` to step through each one you have, starting at the first. Normal and shiny ones are kept apart: press `s` to switch between them.

- **When it was caught**: the date and time, e.g. *25 Nov 2026, 9:41pm (evening)*
- **Its nature**: one of 25 (Jolly, Timid, Brave…), given when it's caught, never changes
- **Catch number**: *your 3rd Pikachu*, or *your very first Pikachu!*
- **Its trainers**: who originally caught it, and the trade that brought it to you
- **Met at level**: your trainer level when it was caught, e.g. *met at Lv 7*
- **Its home region**: Kanto, Johto, Hoenn…

On a Pokemon's page you can also:

- Press `n` to give it a **nickname** (up to 12 letters, numbers, spaces and `.` `'` `-`; leave it empty to clear it). The nickname goes with it if you trade it.
- Press `f` to mark it as a **♥ favourite**. Favourites can't be traded, so you can't send one away by accident. Press `f` again to unmark it.

Pokemon you caught before poketerm 1.0 are all still there, marked *Caught before poketerm 1.0*: their dates and natures weren't recorded back then. When you trade, you choose exactly which one to send, and its story goes with it to the other trainer.

## Trading

Swap Pokemon with a friend on the same network.

1. You both open the Pokedex and press `t`. The trade page shows **Trainers nearby**.
2. Pick your friend and press `Enter` to invite them.
3. Your friend sees your invite. **Check the 6-digit pairing code is the same on both screens**, then they press `y`.
4. You each choose the Pokemon you'll give and press `Enter` (press `/` to search your Pokemon by name). If you have more than one, pick which one - each shows its nature and when it was caught. You see each other's choice straight away and can change yours until you confirm.
5. When you're both happy, you each press `y` to confirm.
6. Your Pokemon is sent off in its Poke Ball, and the one you receive pops out of a Poke Ball.

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

**Trading Achievements**

- Complete your first trade
- Complete 5 trades
- Complete 25 trades
- Complete 100 trades
- Trade with 3 different trainers
- Use all 5 trades in one day
- HIDDEN
- HIDDEN
- HIDDEN
- HIDDEN

There are 52 achievements in total for you to unlock. Some have been left hidden purposly so you can unlock them as you continue collecting!

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

## Credits

- All the pokemon designs, names, branding etc. are trademarks of [The Pokemon Company](https://www.pokemon.com/uk)
- [Pokemon-Colorscripts](https://gitlab.com/phoneybadger/pokemon-colorscripts) for the sprites
- [pokemon-go-colorscripts](https://github.com/scottmckendry/pokemon-go-colorscripts) (MIT) - a Go build of the same sprites that poketerm uses when available for faster startup. The installer pins a reviewed release and verifies its SHA256 checksum, falling back to the Python version otherwise.
- [PokeAPI](https://pokeapi.co/) for the data around the pokemon

## Contributing

- Feel free to fork the repo and open a PR with improvements to the scripts.

## To Do

- Add Gen 9 to the pokedex, open PR needs to be merged with pokemon-colorscripts
