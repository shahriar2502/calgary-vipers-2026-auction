# Project Context — Football Auction Manager

## Project purpose

Build a reliable desktop application for the real Calgary Vipers 2026 football player auction, then polish it into an internship portfolio project. ChatGPT Work maintains requirements, architecture, review, testing guidance, and documentation. Claude Code may continue implementation directly in this repository. The code must remain understandable and should grow milestone by milestone without unnecessary complexity.

## Technology and architecture

- Python desktop application
- CustomTkinter for the later GUI
- Pillow for player images
- JSON persistence during Phase 1
- pytest for automated tests
- Domain models are independent from the UI
- `services/` will contain auction, persistence, and randomization logic in later milestones
- `ui/` and `assets/` are prepared but GUI implementation has not started
- SQLite, web APIs, React, and analytics dashboards are explicitly deferred

## Official branding

- Overall application/tournament identity: **Calgary Vipers**
- Application name: **Football Auction Manager**
- Intended header title: **Calgary Vipers Auction 2026**
- Official logo: `assets/branding/calgary_vipers_logo.png`
- Current theme key: `vipers_dark`
- Branding configuration lives under `branding` in `data/settings.json`; future UI code must load these values rather than hard-code them
- The official logo should be used for the main app identity, top-left application header, and optional splash/welcome screen
- **Calgary Vipers is not an auction team.** It must never replace or be added to Blackout FC, Darkstar FC, Goli Underdogs, or Showstoppers

## Tournament rules

- Total players: 32
- Teams: 4
- Captains: 4, pre-assigned at zero cost
- Auction-eligible players: 28
- Starting auction budget: 100M per team (400M total)
- Final squad target/maximum: 8 players per team
- Each initial roster contains one captain; each team can buy at most 7 players
- A sale must never make a budget negative or produce a roster larger than 8
- A player cannot be sold twice
- Captains never receive a base price, sale price, auction sequence, SOLD, or UNSOLD result

### Teams and captain assignments

| Team | Short name | Captain | Starting budget | Initial squad |
|---|---|---|---:|---:|
| Blackout FC | Samin | Samin Haque | 100M | 1 |
| Darkstar FC | Sabit | Sabit Khan | 100M | 1 |
| Goli Underdogs | Arafat | Arafatul Mamur | 100M | 1 |
| Showstoppers | Riyad | Riyad Zaman | 100M | 1 |

## Current player database

Canonical records live in `data/players.json`. Alternate historical names must not create duplicate records: Rizvi Mahmud = Rizvi Ibrahim; Muhammad Rahmat = Rahmat Ullah; Minhaz Rahman = Minhaz Hamim; Shahriar Arik = Shahriar Anwar Khan.

| # | Player | Short | Pos | OVR | Captain/team |
|---:|---|---|---|---:|---|
| 1 | Samin Haque | Samin | MID | 88 | Blackout FC |
| 2 | Rahmat Ullah | Rahmat | DEF | 89 | — |
| 3 | Sabit Khan | Sabit | MID | 82* | Darkstar FC |
| 4 | Shahriar Anwar Khan | Arik | MID | 85 | — |
| 5 | K M Chisty | Chishty | DEF | 79* | — |
| 6 | Hussain Yeasin | Yeasin | ATT | 81* | — |
| 7 | Ishmam Rahman | Ishmam | ATT | 80* | — |
| 8 | Md Rafiu Hossain | Rafiu | DEF | 78* | — |
| 9 | Hasan Mahtab | Mahtab | ATT | 86 | — |
| 10 | Minhaz Hamim | Minhaz | ATT | 86 | — |
| 11 | Nabil Shahriar | Nabil | GK | 80* | — |
| 12 | Adeeb Ahmed | Adeeb | ATT | 85 | — |
| 13 | Masrur Rahman | Masrur | GK | 82* | — |
| 14 | Arafatul Mamur | Arafat | DEF | 83* | Goli Underdogs |
| 15 | Aafeef Kabir | Afeef | DEF | 78* | — |
| 16 | Aiman Nawar Chowdhury | Aiman | MID | 81* | — |
| 17 | Sajid Khalid | Sajid | MID | 80* | — |
| 18 | Rayhan | Rayhan | GK | 79* | — |
| 19 | Navid Rahman | Navid | ATT | 89 | — |
| 20 | Rizvi Ibrahim | Rizvi | DEF | 90 | — |
| 21 | Fairooz Abir | Abir | MID | 87 | — |
| 22 | Riyad Zaman | Riyad | DEF | 82* | Showstoppers |
| 23 | Faiad Rehman | Faiad | MID | 87 | — |
| 24 | Aldeen | Aldeen | MID | 84* | — |
| 25 | Azmi | Azmi | DEF | 77* | — |
| 26 | Mubasshir | Mubasshir | DEF | 77* | — |
| 27 | Farhan Labib | Farhan | DEF | 78* | — |
| 28 | Jawad | Jawad | GK | 81* | — |
| 29 | Taqi Rahman | Taqi | MID | 79* | — |
| 30 | Mirza | Mirza | MID | 78* | — |
| 31 | Munem | Munem | ATT | 79* | — |
| 32 | Sarim | Sarim | ATT | 78* | — |

`*` indicates an editable placeholder rating. Nabil must remain `GK`; Munem and Sarim must remain `ATT` unless explicitly corrected.

## Rating and position rules

- One overall rating only; no FIFA-style subratings
- Rizvi is 90 and must remain the uniquely highest-rated player unless explicitly changed
- Current top tier: Rizvi 90; Navid and Rahmat 89; Samin 88; Abir and Faiad 87; Mahtab and Minhaz 86; Adeeb and Arik 85
- Remaining placeholder ratings stay within 75–84 until updated
- Allowed positions only: `GK`, `DEF`, `MID`, `ATT`
- Position colors: GK purple, DEF blue, MID green, ATT orange

## Future auction behavior (not implemented in Milestone 1)

- Build one shuffled queue containing all 28 eligible players exactly once
- Exclude all captains; optional seeds support reproducible tests
- Keep the full upcoming order hidden in tournament mode
- Reveal one player at a time
- Bid increments initially: 1M, 2M, 5M
- SOLD validates leader, price, budget, roster space, eligibility, captain status, and prior status
- UNSOLD players do not automatically return in Phase 1
- Auto-save after SOLD and UNSOLD; restore teams, queue position, results, and history after restart

## Future UI requirements (not implemented)

- Dark sports-tech style, green accent, responsive at 1366×768 and 1920×1080
- Use the configurable Calgary Vipers logo and header title for the overall application identity
- Navigation: Live Auction, Players & Setup, Teams, Auction History, Reports, Settings
- Large projector-friendly current-player card showing only photo, name, position, and OVR
- Four visible team panels with captain, remaining budget, and squad size
- Missing photos use a placeholder and must never crash the app
- SOLD is green; UNSOLD is red

## Current milestone

Milestone 1 — core project foundation.

## Completed in Milestone 1

- Repository/folder structure
- Player, Team, Auction, and AuctionHistoryEntry models
- Model validation and dictionary serialization
- Full player/team/settings JSON configuration
- Official Calgary Vipers logo asset and configurable branding metadata
- Empty initial auction state
- Core tests for roster counts, captain rules, required positions, rating ranges, budgets, squad limits, duplicate protection, and model serialization
- README and project context

## Key implementation decisions

- Money is stored as integer millions (`14` means 14M) to avoid floating-point currency errors.
- Rosters store canonical player IDs; the captain is always the first initial member.
- Enums constrain player position, player auction status, and overall auction status.
- `Team.add_purchased_player()` protects budget, squad size, and duplicate membership at the model layer.
- The Auction model stores state only. Queue creation, bidding, selling, marking unsold, and persistence belong to services in a later approved milestone.
- Tournament-specific counts, budgets, squad limits, position colors, and increments are centralized in `data/settings.json`.
- Branding is stored as configuration (`app_name`, `branding_name`, `header_title`, `logo_path`, and `theme_name`) so a later logo or identity change does not require rewriting UI components.

## Known issues / intentional gaps

- Placeholder ratings are provisional and need tournament-organizer approval.
- No GUI exists yet.
- No randomization, bidding, sale/unsold workflow, or persistence service exists yet.
- Cross-model operations (for example updating Player and Team atomically) are deliberately deferred.
- Base prices are not assigned because the specification provides examples, not final values.
- Player photos, team assets, and placeholder artwork are not added; the official branding logo is present.

## Next task (requires approval)

Define Milestone 2 scope before implementation. Do not begin it automatically.
