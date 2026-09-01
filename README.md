# Football Auction Manager

A desktop football-auction application for the Calgary Vipers 2026 tournament. The immediate goal is a reliable tournament-ready tool; the longer-term goal is a polished software portfolio project.

Calgary Vipers is the application and tournament branding identity. It is not an auction team. The official logo is stored at `assets/branding/calgary_vipers_logo.png`, and its UI-facing values are configurable in `data/settings.json`.

## Current state

Milestone 1 is complete: repository structure, configurable tournament data, core domain models, and model tests. No GUI or auction workflow service has been implemented yet.

## Tournament snapshot

- 32 total players: 4 pre-assigned captains and 28 auction-eligible players
- 4 teams, each beginning with a 100M auction budget
- 8-player final squad target: 1 captain plus up to 7 purchases
- Captains never enter the auction and cost nothing
- Positions: `GK`, `DEF`, `MID`, `ATT`

## Project structure

```text
football-auction-manager/
├── main.py
├── data/                 # Player, team, settings, and auction-state JSON
├── models/               # GUI-independent domain models and validation
├── services/             # Reserved for later business/persistence logic
├── ui/                   # Reserved for the later CustomTkinter GUI
├── assets/               # Official branding, player/team images, placeholders
├── tests/                # pytest model and configuration tests
├── PROJECT_CONTEXT.md    # Authoritative project rules and status
├── requirements.txt
└── README.md
```

## Setup and tests

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

Running `python main.py` currently prints a Milestone 1 status message. The actual application entry point will be connected to the GUI in a later approved milestone.

## Development rules

- Read `PROJECT_CONTEXT.md` before changing the repository.
- Keep business logic independent from GUI callbacks.
- Treat `data/settings.json` as the source for configurable tournament values.
- Load the application name, branding name, header title, theme, and logo path from the `branding` settings object; do not hard-code them into future UI widgets.
- Keep Calgary Vipers separate from the four auction teams.
- Do not auction captains or reduce budgets for captain assignments.
- Update tests and `PROJECT_CONTEXT.md` whenever rules or implementation decisions change.
