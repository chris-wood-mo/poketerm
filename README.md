<div align="center">

<img src="assets/logo.svg" alt="poketerm" width="560">

**Catch Pokémon every time you open a terminal, and collect them in a Pokédex.**

[![Latest release](https://img.shields.io/github/v/release/chris-wood-mo/poketerm?include_prereleases&label=release)](https://github.com/chris-wood-mo/poketerm/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
![Shell](https://img.shields.io/badge/shell-zsh-89e051)

</div>

https://github.com/user-attachments/assets/54460f60-bf9c-4139-9962-08387df8d2a3

Every new terminal catches a random Pokémon, sometimes a rare shiny, and draws its sprite. Your catches build up in a Pokédex drawn in the terminal as a handheld device. From there you can trade with friends on your network, look back through every Pokémon's story, and level up your trainer.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Trading](#trading)
- [Your Pokémon's stories](#your-pokémons-stories)
- [Catch history](#catch-history)
- [Settings](#settings)
- [Moving to a new computer](#moving-to-a-new-computer)
- [Achievements](#achievements)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Credits](#credits)

## Features

- **A catch in every terminal.** Every new terminal catches a random Pokémon from Generations 1-8 (905 in all), with a 1 in 4,096 chance of a shiny.
- **A handheld Pokédex.** Browse your collection on a Pokédex drawn in the terminal. It adapts to split and narrow windows, and you can search by name or number.
- **Local trading.** Swap Pokémon with trainers on the same network. Trades are encrypted, both sides confirm them, and they can never duplicate or lose a Pokémon.
- **Every Pokémon has a story.** Each one keeps its catch date, nature, catch number and trainer history. You can give it a nickname or mark it as a favourite.
- **A trainer profile.** Earn XP, level up, keep a daily streak, and unlock 52 achievements, some of them hidden.
- **Catch history.** See your first, latest and rarest catch, your busiest day, and when in the day you catch.
- **A settings page.** Choose which generations you catch from, and turn system details, animations and update checks on or off.
- **Easy to keep.** One command updates poketerm and keeps a backup of your progress. One file moves everything to a new computer.

## Requirements

| | |
|---|---|
| **OS** | macOS (Apple Silicon or Intel) or Linux (x86-64 or ARM) |
| **Shell** | zsh |
| **Tools** | `curl` and `git` |
| **Optional** | [fastfetch](https://github.com/fastfetch-cli/fastfetch) (or neofetch) to show system details beside each catch |

The installer sets up everything else, including poketerm's own Python (see [poketerm's own Python](#poketerms-own-python)).

## Installation

```bash
git clone https://github.com/chris-wood-mo/poketerm.git
cd poketerm
./install.sh
source ~/.zshrc
```

That's it. Open a new terminal to catch your first Pokémon. The first time, poketerm asks for your trainer name, region and home town.

To catch only from certain generations, add `--gen` (you can change this later in [Settings](#settings)):

```bash
./install.sh --gen 1      # Generation 1 only
./install.sh --gen 2-5    # Generations 2 to 5
```

### poketerm's own Python

poketerm brings its own Python, so it behaves the same on every machine whatever Python (if any) your system has.

- **What gets downloaded:** the installer downloads it once: Python 3.14 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), about 27-37 MB, into `~/.local/share/poketerm-python`.
- **Trading's encryption:** the encryption package trading uses ([cryptography](https://cryptography.io)) comes with it.
- **Checked downloads:** every download is checked against a SHA256 hash pinned in `install.sh`, and anything that doesn't match is rejected.
- **Separate from your own Python:** nothing you install with pip affects it.

On systems it isn't built for (such as 32-bit or Alpine Linux), poketerm uses your system's `python3` instead. Trading then also needs Python 3 with TLS 1.3 and the `openssl` command.

## Usage

poketerm runs by itself every time you open a terminal. Open the Pokédex with:

```bash
pokedex           # open the Pokédex
pokedex 3         # open it at Generation 3
```

### Pokédex keys

| Key | Action |
|---|---|
| `↑` `↓` | Move through the list |
| `←` `→` | Previous / next page |
| `Enter` | Open the selected Pokémon's entry |
| `/` | Search by name or number in every generation, e.g. `pika`, `mr mime` or `150` |
| `p` | Trainer profile |
| `h` | [Catch history](#catch-history) |
| `a` | Achievements |
| `t` | [Trading](#trading) |
| `s` | [Settings](#settings) |
| `q` | Quit |

### Commands

| Command | Description |
|---|---|
| `poketerm` | Catch a Pokémon now (also happens on every new terminal) |
| `poketerm --gen 1` / `--gen 2-5` | Catch from specific generations for this run only |
| `poketerm --update` | Update to the latest version ([more](#updating)) |
| `poketerm --export` / `--import FILE` | Move to a new computer ([more](#moving-to-a-new-computer)) |
| `poketerm --remove NAME` | Remove a Pokémon you added to the Pokédex by hand |
| `poketerm --fix-pokedex` | Accept a hand-edited Pokédex file |
| `poketerm --version` | Show the installed version |
| `poketerm --help` / `pokedex --help` | Show all options |

## Trading

Swap Pokémon with a friend on the same network.

1. You both open the Pokédex and press `t`. The trade page lists **trainers nearby**.
2. Choose your friend and press `Enter` to invite them.
3. **Check that the 6-digit pairing code matches on both screens.** Your friend then presses `y` to accept.
4. Each of you chooses a Pokémon to give and presses `Enter`. Press `/` to search your Pokémon. If you have several of that Pokémon, you pick which one: each shows its nature and catch date.
5. When you're both happy, you each press `y` to confirm. Your Pokémon leaves in its Poké Ball and the new one arrives in another.

Good to know:

- **Limit:** you can make **5 trades a day**.
- **Shinies and favourites:** shiny Pokémon stay shiny, and ♥ favourites can't be traded.
- **Visibility:** only trainers with their trade page open can be seen. Close it and you're invisible.
- **Friend not listed:** press `c` and type the address shown on their trade page.
- **On macOS:** allow your terminal app under **System Settings → Privacy & Security → Local Network**. If asked, let Python accept incoming connections.
- **Dropped connections:** if the connection drops at the last moment, nothing is lost. The trade finishes the next time you're both on the trade page.

<details>
<summary>How trades are kept safe</summary>

- **Encryption:** every connection is encrypted (TLS 1.3), and each trainer has their own identity key.
- **Pairing code:** the code you compare on screen confirms you're connected to your friend and nobody else.
- **All or nothing:** a trade completes on both sides or not at all, even if a laptop closes mid-trade. An unfinished trade is settled automatically the next time you connect.
- **Tamper-proof history:** a trade history that's been edited is detected, and trading pauses.

</details>

## Your Pokémon's stories

Every Pokémon you catch is remembered individually. Open one in the Pokédex and use `←` `→` to step through each one you have. Press `s` to switch between normal and shiny.

- **Caught:** the date and time, e.g. *25 Nov 2026, 9:41pm (evening)*
- **Nature:** one of 25 (Jolly, Timid, Brave…), set when it's caught
- **Catch number:** e.g. *your 3rd Pikachu*, or *your very first Pikachu!*
- **Trainers:** who originally caught it, and the trade that brought it to you
- **Met at:** your trainer level when it was caught
- **Home region:** Kanto, Johto, Hoenn…

On a Pokémon's page, press `n` to give it a **nickname** (it keeps it if traded), or `f` to mark it as a **♥ favourite**. Favourites can't be traded.

Pokémon caught before poketerm 1.0 are all still there, marked *Caught before poketerm 1.0*, because their dates and natures weren't recorded back then.

## Catch history

Press `h` in the Pokédex to see your catching habits:

- **First and latest catch**, with the date and time
- **Rarest catch:** shinies first, then Mythical, Legendary and pseudo-legendary Pokémon, then any you've only caught once
- **Busiest day:** the day you caught the most
- **When you catch:** your catches split into morning, afternoon, evening and night, and what that makes you, e.g. *You're a night-time catcher!*

Every Pokémon you've caught counts, including ones you've since traded away. Pokémon received in trades don't count, because another trainer caught them.

## Settings

Open the Pokédex and press `s`. Use `↑` `↓` to move and `Enter` to change a setting. Changes are saved immediately and apply from the next terminal you open.

| Setting | Description |
|---|---|
| **Generations** | Which generations new terminals catch from. At least one must stay on; `a` turns them all back on |
| **System details** | Show or hide system information (OS, host, …) beside each catch |
| **Trade animations** | Play the Poké Ball animation when a trade completes, or skip it |
| **Daily update check** | Check once a day for a new version of poketerm |

The bottom of the settings page shows your poketerm version. Settings are stored in `~/.local/share/poketerm/settings.conf` and kept when you update.

## Moving to a new computer

Take everything with you in one file: your Pokédex, trainer profile, achievements, every Pokémon's story, your settings and your trading identity.

```bash
# On your old computer
poketerm --export                     # saves poketerm-<name>-<date>.poketerm

# On your new computer, after installing poketerm
poketerm --import poketerm-ash-2026-11-25.poketerm
```

- **Encrypted:** the file is protected by a passphrase you choose (at least 8 characters). It holds your trading identity, so keep it private.
- **Checked first:** a wrong passphrase, or a damaged or edited file, is refused before anything changes.
- **Asks before replacing:** the import shows what's in the file and what's on the new computer. It backs up the new computer's progress first.
- **Same identity:** other trainers still recognise you. After moving, trade from the new computer only.

## Achievements

There are 52 achievements to unlock. Some are hidden: you'll find them as you collect.

<details>
<summary>See the achievement list</summary>

**Pokédex milestones**
- First Pokémon caught
- 50, 100 and 905 Pokémon caught
- Complete each of Generations 1-8
- 2 hidden

**Catching**
- 3, 7, 30, 100 and 365 day catch streaks
- Catch 5 and 100 shiny Pokémon
- Catch 5 and 10 Pokémon in a day
- Catch the same Pokémon 10 and 25 times
- 2 hidden

**Trading**
- Complete your first, 5th, 25th and 100th trade
- Trade with 3 different trainers
- Use all 5 trades in one day
- 4 hidden

**Levels**
- Reach level 10 and 25
- Reach 1,000, 10,000 and 50,000 total XP
- 2 hidden

**Special collections**
- 8 hidden

</details>

## Updating

```bash
poketerm --update
```

Your progress and settings are kept, and backed up to `~/.local/share/poketerm/backups` first. If an update fails part-way, the previous version is restored automatically. poketerm also checks once a day and tells you when a new version is available. You can turn this off in [Settings](#settings) or with `POKETERM_NO_UPDATE_CHECK=1`.

```bash
poketerm --update --check     # check whether an update is available
poketerm --update 0.0.7       # install a specific version (add --force to go back)
poketerm --update --beta      # get pre-release versions early
```

> **Upgrading from 0.0.6 or earlier?** Those versions can't update themselves. Update once with `cd poketerm && git pull && ./install.sh --update`. After that, `poketerm --update` works.

## Uninstalling

```bash
cd poketerm
./install.sh --uninstall
```

This removes poketerm and its data. Afterwards, delete the block between `# >>> POKETERM SCRIPT BEGIN >>>` and `# <<< POKETERM SCRIPT END <<<` from your `~/.zshrc`.

## Troubleshooting

<details>
<summary><b>Powerlevel10k shows "Console output during zsh initialization detected"</b></summary>

poketerm prints when a terminal opens, so its block in `~/.zshrc` must come before Powerlevel10k's instant prompt. The installer places it there, so updating fixes it. To fix it by hand, move the `POKETERM SCRIPT` block above the line `# Enable Powerlevel10k instant prompt`.

</details>

<details>
<summary><b>Trading can't find other trainers</b></summary>

- Check that you're both on the same network and both have the trade page open.
- On macOS, allow your terminal app under **System Settings → Privacy & Security → Local Network**.
- Some networks (such as guest or office Wi-Fi) block devices from seeing each other. Press `c` and type the address shown on your friend's trade page instead.

</details>

## Development

```bash
./test.sh                         # end-to-end tests in a throwaway HOME (-v for details)
poketerm --update --from .        # install this checkout as a normal build
./release.sh 1.0.0-beta.1         # publish a pre-release (only --beta users get it)
./release.sh 1.0.0                # publish a stable release from main
./release.sh 1.0.0 --dry-run      # run every check without publishing
```

`release.sh` checks:
- the version is newer than the last release;
- there are no uncommitted changes;
- the full test suite passes.

It then updates `VERSION`, tags the release, pushes it, and creates the GitHub release. That needs the [GitHub CLI](https://cli.github.com/).

Contributions are welcome: fork the repo and open a pull request.

### Roadmap

- Generation 9 (waiting on support in pokemon-colorscripts)

## Credits

- Pokémon and all related names, designs and branding are trademarks of [The Pokémon Company](https://www.pokemon.com/uk). poketerm is a fan project and isn't affiliated with or endorsed by them.
- [pokemon-colorscripts](https://gitlab.com/phoneybadger/pokemon-colorscripts) for the sprites.
- [pokemon-go-colorscripts](https://github.com/scottmckendry/pokemon-go-colorscripts) (MIT), a faster Go build of the same sprites. The installer pins a reviewed release and checks its SHA256.
- [PokéAPI](https://pokeapi.co/) for Pokémon data.

## License

[MIT](LICENSE) © 2025 Christopher Wood
