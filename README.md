# Calgary Vipers Auction 2026

A custom desktop football auction management system built in Python for the **Calgary Vipers recreational soccer community**.

The application was created to manage our player auctions, team budgets, captain bidding, match-based transfer funds, and second transfer-window auction.

It was successfully used to run both the **first live player auction** and the **second transfer-window auction** for our tournament.


## About the Project

Calgary Vipers is a recreational soccer community where we regularly play together and organize our own tournaments.

I wanted to build something useful for the group rather than creating a project only for practice, so I developed a complete auction management system around the way our tournament actually works.

The project started as a simple player-auction tool and gradually grew into a full tournament auction platform with:

- live player bidding
- captain phone bidding
- team budget management
- roster validation
- persistent tournament sessions
- auction history
- reports and analytics
- match-result based transfer funds
- player releases and refunds
- a full second transfer-window auction
- final updated team rosters

The application was used during the real tournament rather than only being demonstrated with test data.


## Tech Stack

- **Python**
- **CustomTkinter**
- **FastAPI**
- **HTML / JavaScript** for captain phone bidding
- **JSON** persistence
- **Pytest**
- **Pillow**
- **PyInstaller**


## Tournament Structure

The tournament contains:

- 32 total players
- 4 teams
- 4 pre-assigned captains
- 28 first-auction players
- 100M starting budget per team
- 8-player squads
- exactly one goalkeeper per team

Captains are assigned to their teams before the auction and do not consume auction budget.

### First-auction base prices

- Goalkeeper: **4M**
- Outfield player: **2M**

The auction also uses a dynamic completion-reserve rule that prevents teams from spending so much on one player that they can no longer afford to complete their required squad.


# Features

## Live First Auction

The main auction screen manages the complete first-auction workflow.

Features include:

- randomized player order
- live player cards
- SOLD / UNSOLD handling
- automatic re-auction rounds
- team eligibility validation
- current bid and leading team
- player-specific maximum legal bids
- team budget tracking
- squad-size tracking
- mandatory goalkeeper validation
- automatic auction completion
- blocked-state protection for impossible auction states

UNSOLD players automatically return in later auction rounds until every eligible player has been sold.


## Captain Phone Bidding

Captains can participate in the auction from their phones while connected to the same local Wi-Fi network as the organizer laptop.

Each team receives its own private PIN.

After logging in, a captain's phone is permanently associated with that team for the session.

Captain phones can:

- view the current player
- view current bid and leading team
- view their own team budget
- view roster size
- view maximum legal bid
- place +1M / +2M / +5M bids

Captain phones **cannot** finalize a player sale.

The organizer always remains responsible for SOLD / UNSOLD decisions.

The system also supports mixed-mode bidding:

- 0 phones connected
- 1 phone connected
- several phones connected
- all 4 captains connected

The organizer can manually bid for any team at any time, even when that captain is connected by phone.


## Persistent MOCK and LIVE Sessions

The app supports two session types:

### MOCK

Used for practice, testing, and rehearsing tournament scenarios.

### LIVE

Used for the official tournament record.

Auction sessions autosave after important transactions and can be reopened after the application closes.

Saved data includes:

- auction progress
- player queue
- team budgets
- rosters
- auction history
- match results
- transfer-window setup
- second-auction state

MOCK and LIVE sessions are kept separate.


## Teams

The Teams screen displays all four squads.

Before the second auction, it shows the first-auction teams.

During and after the second auction, it switches to the **current tournament roster** rather than displaying outdated first-auction ownership.

After the transfer window is complete, each team shows:

- final roster
- captain
- goalkeeper
- second-auction starting budget
- second-auction spending
- final remaining budget
- 8 / 8 squad status

Players acquired during the second auction can also be identified as new transfers.


## Player Cards

A visual gallery displays every tournament player with:

- player photo
- name
- position
- overall rating
- captain status
- historical FPL information where available

Players can be searched, filtered, sorted, and opened in a larger detail view.


## Auction History

The first-auction history is preserved as a chronological transaction record.

It records:

- SOLD attempts
- UNSOLD attempts
- player
- team
- sale price
- auction round

A player who goes UNSOLD and is later purchased keeps both transactions in the historical record.

The original first-auction ownership and purchase prices are never overwritten by the second auction.


## Reports

The Reports screen provides an auction analytics dashboard including:

- total players sold
- total spending
- average sale price
- median sale price
- highest sale
- lowest sale
- most expensive purchase
- highest-rated purchases
- team spending comparisons
- remaining budgets
- squad sizes
- re-auction statistics


# Match Results and Transfer Budgets

After the first auction, tournament match results can be entered directly into the application.

Match-money rules:

- **Win: +4M**
- **Draw: +2M per team**
- **Loss: +1M**

The application calculates:

`Current Transfer Budget = First Auction Remaining Budget + Match Earnings`

Match results can be:

- added
- edited
- deleted

Changing a result recalculates the financial totals rather than adding duplicate awards.

The original first-auction remaining budget always stays preserved separately.


# Second Transfer Window

The application includes a full second-auction workflow.

## Player Releases

Each team releases exactly four players.

The application automatically prevents the release of:

- the team captain
- the team goalkeeper

Other eligibility decisions can be handled by the tournament organizer.

The release screen shows:

- current team roster
- selected releases
- original first-auction purchase price
- refund amount
- projected second-auction starting budget

Exactly 16 players enter the second-auction pool.


## Release Refunds

A released player refunds the amount their team originally paid for them during the first auction.

The second-auction budget is calculated as:

`Second Auction Starting Budget = Current Transfer Budget + Release Refunds`

The original first-auction transaction remains unchanged for historical purposes.


## Second Auction

The second auction has its own independent state, budgets, rosters, and transaction history.

Each team begins with four retained players and four open squad positions.

The second auction supports:

- randomized 16-player pool
- SOLD / UNSOLD
- re-auction rounds
- squad-size validation
- budget validation
- completion-reserve logic
- captain phone bidding
- manual organizer bidding
- interruption and resume
- final 8-player squads

The organizer can close and reopen the application during the second auction and continue from the saved state.


## Second-Auction Phone Bidding

Captain phone bidding works during the second auction using the same team PIN system.

The Second Auction screen also displays a live captain connection panel showing the status of all four teams.

Examples:

- CONNECTED
- RECENTLY ACTIVE
- MANUAL / OFFLINE

Even if a phone disconnects, the organizer can immediately continue bidding manually for that team.


# Real Tournament Usage

The application was used successfully to run the actual Calgary Vipers tournament auctions.

The first auction handled:

- 28 auction players
- 4 teams
- multiple auction rounds
- live team budgets
- captain phone bidding
- final squad creation

After several gameweeks, match results were recorded to calculate transfer-window funding.

The same tournament session was then used to prepare and run the second transfer-window auction, including player releases, refunds, captain bidding, and final squad updates.

This project therefore went beyond a prototype and was used as an operational tool for the soccer community it was designed for.


# Project Structure

text
football-auction-manager/
├── main.py
├── data/                  # Canonical tournament data
├── models/                # Domain models
├── services/              # Auction and business logic
├── ui/                    # CustomTkinter application
│   └── screens/
├── assets/                # Branding and player images
├── tests/                 # Pytest test suite
├── PROJECT_CONTEXT.md     # Detailed internal architecture/rules
├── requirements.txt
├── requirements-build.txt
├── calgary_vipers_auction.spec
├── build_windows.bat
└── README.md


Runtime folders such as `saves/`, `config/`, and logs are excluded from the repository.


# Running the Project

Python 3.11+ is recommended.

### Create a virtual environment

```bash
python -m venv .venv
```

### Activate it

Windows:

```bash
.venv\Scripts\activate
```

macOS / Linux:

```bash
source .venv/bin/activate
```

### Install dependencies

```bash
python -m pip install -r requirements.txt
```

### Run the application

```bash
python main.py
```

---

# Running Tests

```bash
python -m pytest
```

The project includes tests covering:

- auction transactions
- budget rules
- roster validation
- persistence
- match-result accounting
- player releases
- refund calculation
- second-auction bidding
- phone authentication
- mixed phone/manual bidding
- final current-roster resolution


# Captain Phone Bidding Setup

The organizer laptop and captain phones must be connected to the same local Wi-Fi network.

1. Launch the application.
2. Open **Settings**.
3. Start **Captain Phone Bidding**.
4. Copy the displayed LAN address.
5. Privately send each captain their team PIN.
6. Captains open the LAN address in a mobile browser.
7. Captains enter their PIN.
8. Connection status appears in the desktop application.
9. Captains place bids from their phones.
10. The organizer controls SOLD / UNSOLD from the laptop.

Phone bidding is optional. The auction can always be completed entirely from the organizer laptop.


# Design Principles

The project follows several important rules:

- business logic stays outside GUI callbacks where practical
- first-auction history remains immutable
- second-auction history is stored separately
- MOCK and LIVE sessions remain isolated
- captain PINs are never stored in public repository data
- saved LIVE tournament sessions are git-ignored
- the organizer always retains manual auction control
- captain phones can bid but cannot finalize transactions

---

# Future Development

The main tournament workflow is complete.

Possible future improvements include:

- additional tournament formats
- more reusable tournament configuration
- improved packaged Windows release
- additional analytics
- broader support for other recreational leagues


## Why I Built It

This project started because I wanted to build something useful for the soccer community I play with every week.

Instead of making a standalone portfolio project with no real users, I wanted to solve an actual problem for our tournament and create something everyone could interact with during the event.

Seeing the system go from an idea, to development and testing, to successfully running both of our real auctions was the most rewarding part of the project.
