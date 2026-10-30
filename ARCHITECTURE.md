# Calgary Vipers Auction 2026 — Architecture

## Overview

Calgary Vipers Auction 2026 is a Python desktop application for managing player auctions and transfer-window activity for a recreational soccer tournament.

The application handles:

- first-auction player bidding
- team budgets and roster rules
- captain phone bidding over local Wi-Fi
- persistent MOCK and LIVE tournament sessions
- match-result based transfer funding
- player releases and refunds
- a second transfer-window auction
- final current team rosters
- auction history and reports

The desktop interface is built with CustomTkinter. Captain phones connect through a lightweight FastAPI server running locally on the organizer's laptop.

## Project Structure

```text
main.py
models/
services/
ui/
assets/
data/
tests/
```

### `models/`

Contains the core domain objects used by the application, including players, teams, first-auction state, second-auction state, auction history, and match results.

Models are kept independent from the GUI so tournament logic can be tested separately.

### `services/`

Contains the application's business logic, including:

- loading tournament configuration
- player and team initialization
- auction randomization
- bidding validation
- SOLD / UNSOLD processing
- persistence and recovery
- match-result calculations
- second-auction release and refund calculations
- second-auction bidding
- captain authentication
- local phone-bidding server
- current-roster resolution

### `ui/`

Contains the CustomTkinter application and screens:

- Live Auction
- Second Auction
- Player Cards
- Players & Setup
- Teams
- Auction History
- Reports
- Match Results
- Settings

The UI displays tournament state while the service layer enforces tournament rules.

## First Auction

The first auction begins with four captains already assigned to their teams. The remaining 28 players enter a randomized auction queue.

Each team:

- begins with a 100M budget
- must finish with 8 players
- must contain exactly one goalkeeper
- may not purchase another goalkeeper after acquiring one

Base prices are:

- Goalkeeper: 4M
- Outfield player: 2M

The application calculates a maximum legal bid for each team based on its remaining budget and required roster positions. This prevents a team from making a purchase that would leave it unable to complete its squad.

UNSOLD players automatically return in a later re-auction round.

## Persistence

Tournament sessions are separated into two modes:

### MOCK

Practice and testing sessions.

### LIVE

The official tournament session.

Auction state is saved after important transactions so the application can be closed and reopened without losing tournament progress.

Persisted information includes:

- auction queue and progress
- team budgets
- rosters
- transaction history
- match results
- second-auction setup
- second-auction state

Runtime tournament saves are excluded from version control.

## Captain Phone Bidding

Captain bidding uses a local FastAPI server running on the organizer laptop.

Phones connect using the laptop's LAN address while on the same Wi-Fi network. Each captain authenticates using a team-specific PIN.

The phone client can:

- view the current player
- view the current highest bid
- view the leading team
- view its own budget
- view its maximum legal bid
- place bids

The organizer laptop remains authoritative. Captain phones cannot mark players SOLD or UNSOLD, modify rosters directly, or bid as another team.

Manual desktop bidding remains available at all times, including when phones are disconnected.

## Match Results and Transfer Funding

After the first auction, match results can be entered into the application.

Tournament awards are:

- Win: 4M
- Draw: 2M
- Loss: 1M

The transfer budget is calculated as:

```text
First Auction Remaining Budget
+ Match Earnings
= Current Transfer Budget
```

Editing or deleting a result recalculates the totals rather than adding duplicate awards.

## Second Auction

The second auction is intentionally stored separately from the first auction so the original first-auction ownership, prices, budgets, and transaction history remain unchanged.

Before the second auction:

- each team releases exactly four players
- captains cannot be released
- goalkeepers cannot be released
- released players refund their original first-auction purchase price

The second-auction starting budget is:

```text
Current Transfer Budget
+ Released Player Refunds
= Second Auction Starting Budget
```

Once the second auction begins, the starting budget is frozen.

Each team begins the second auction with four retained players and must return to a final squad of eight.

The second auction maintains its own:

- budgets
- active rosters
- auction queue
- live bids
- transaction history

## Current Team Resolution

The Teams screen represents the current tournament state.

Before the second auction begins, it displays the first-auction roster. Once the second auction starts, it uses the second-auction active roster instead.

After completion, it displays the final tournament squads and remaining second-auction budgets.

## Safety and Data Separation

Several areas are intentionally kept separate:

- MOCK sessions never modify LIVE tournament data.
- First-auction history is preserved after the second auction.
- Runtime saves are not committed to Git.
- Captain PIN configuration is stored locally.
- Captain phones can submit bids but cannot finalize transactions.
- Manual organizer controls remain available if phone connectivity fails.

## Testing

The project includes automated tests for:

- auction rules
- budget validation
- roster validation
- persistence
- match-result accounting
- release and refund calculations
- second-auction logic
- captain authentication
- phone bidding
- mixed manual and phone bidding
- current-roster resolution

The application was also tested using full MOCK tournament runs before being used for the live tournament.

## Design Goal

The project keeps tournament rules and business logic separate from the desktop UI where practical.

The main goal is reliability during a live event: tournament state should remain recoverable, auction rules should be enforced consistently, and the organizer should always retain control even if optional phone bidding becomes unavailable.
