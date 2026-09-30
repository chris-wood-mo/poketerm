# poketerm

Collectable terminal Pokedex that captures sprites from the external tool `pokemon-colorscripts`.

This repository provides a simple service to "catch" Pokemon created from pokemon-colorscripts, keep a persistent pokedex, and view per-generation progress.

<https://github.com/user-attachments/assets/9277514e-d871-4d28-a3ae-59980b39fec3>

## Features

- Capture a random Pokemon sprite (normal or shiny) using `pokemon-colorscripts`.
- Persist caught Pokemon to a user pokédex file.
- Keep per-generation ordering using the generation lists in gen_files/.
- View a generation-specific catch progress report.

## Requirements

- macOS or Linux
- zsh
- python3

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
cd poketerm
git pull
./install.sh --update
source ~/.zshrc
```

Updating to specific generations:

```bash 
cd poketerm
git pull
./install.sh --update --gen 1
source ~/.zshrc
```

```bash
cd poketerm
git pull
./install.sh --update --gen 2-5
source ~/.zshrc
```

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

# Remove a manually added Pokemon from the Pokedex
poketerm --remove pikachu
```

When the shell starts, Poketerm will auto-run and keep catching Pokemon in your terminal.

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
- [PokeAPI](https://pokeapi.co/) for the data around the pokemon

## Contributing

- Feel free to fork the repo and open a PR with improvements to the scripts.

## To Do

- A search feature for the pokedex
- Add Gen 9 to the pokedex, open PR needs to be merged with pokemon-colorscripts
