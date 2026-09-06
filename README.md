# Football Auction Manager

A desktop football-auction application for the Calgary Vipers 2026 tournament. The immediate goal is a reliable tournament-ready tool; the longer-term goal is a polished software portfolio project.

Calgary Vipers is the application and tournament branding identity. It is not an auction team. The official logo is stored at `assets/branding/calgary_vipers_logo.png`, and its UI-facing values are configurable in `data/settings.json`.

## Current state

Milestones 1–8 are complete: repository structure and domain models, a verified/finalized 32-player database and four-team initialization, a randomized hidden auction queue/order engine, a basic CustomTkinter application shell, a real, read-only Players & Setup screen, a real Teams screen (read-only for edits, but live-updating once an auction is running), a SOLD/UNSOLD auction transaction service, and a real, interactive **Live Auction** screen — start a session, reveal the current player, select a winning team, enter a price, confirm SOLD or UNSOLD, and watch team budgets/rosters/progress update immediately. Milestone 8.1 corrected the tournament rules on top of that: UNSOLD players automatically return in a re-auction round instead of ending the auction, a team may hold at most one goalkeeper and every final squad must contain exactly one, a team at 8/8 players is no longer eligible to bid, and a true dead-end (no eligible team can take the remaining players) surfaces as a clear "auction blocked" state instead of looping or silently completing. A pre-Milestone-9 fix then closed a budget gap: a team can no longer spend so much on one player that it can't afford the minimum price for every roster slot it still needs. **Milestone 9 adds full persistence**: every auction is either a MOCK (practice) or LIVE (tournament) session, autosaves after every SOLD/UNSOLD/round-transition, and survives a full app restart exactly as it was. A September 2026 follow-up added **Player Cards** — a visual gallery/showcase of all 32 players (separate from the administrative Players & Setup table) with search/filter/sort and a click-to-expand detail view, plus a nullable last-season FPL points statistic per player. A further September 2026 follow-up turned **Auction History** into a real, read-only, chronological transaction-timeline screen for the current session (search/filter by result/round/team, summary cards, oldest/newest sort). A further September 2026 follow-up turned **Reports** into a real, read-only auction summary/analytics dashboard (KPIs, team comparisons, price/FPL/OVR analytics, re-auction summary). A further September 2026 follow-up turned **Settings** into a real organizer configuration/diagnostics screen (fullscreen/display, SOLD/UNSOLD confirmation preferences, session/save status, tournament diagnostics, and a locked, read-only tournament-rules summary). Every sidebar route now has a real screen. A final September 2026 follow-up, **Captain Phone Bidding — Phase 1**, proved that a real phone on the same Wi-Fi network can place bids for one team while the organizer bids for the other teams from the laptop — see "Captain Phone Bidding — Local Test" below; the organizer's laptop remains the sole authority for finalizing SOLD/UNSOLD, and the desktop-only workflow above still works unchanged with the phone-bidding server off.

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
├── data/                 # Player, team, and settings JSON (read-only canonical reference data)
├── saves/                # MOCK/LIVE auction session saves (git-ignored, created on first save)
├── models/               # GUI-independent domain models and validation
├── services/             # Business logic: config/player/team loading, filtering, validation, roster resolution, randomized auction queue, SOLD/UNSOLD transactions, shared AuctionSession, persistence, live-bid validation, captain phone-bidding server
├── ui/                   # CustomTkinter GUI: main window, theme, shared widgets, navigation, screens/
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

Running `python main.py` launches the Calgary Vipers Auction 2026 desktop window: a branded header and sidebar navigation (Live Auction, Player Cards, Players & Setup, Teams, Auction History, Reports, Settings). Live Auction (shown by default) opens on a **session launcher** when no auction is currently active: NEW MOCK AUCTION / NEW LIVE AUCTION buttons, plus a "Saved Sessions" panel listing the current LIVE session (if any) and every saved MOCK practice session with a Resume/Delete action each. Starting a new LIVE auction while one already exists asks for confirmation first — the old one is archived, never silently overwritten. Once a session is active, a compact "MOCK SESSION" / "LIVE SESSION" badge and an autosave-status indicator ("Autosaved HH:MM:SS" or a prominent "SAVE FAILED") sit next to the round/progress line, and the screen otherwise looks and works exactly as before: it reveals one randomized player at a time on a large sports-broadcast-style card — real photo (all 32 canonical players now have one; a missing/unreadable photo falls back to an initials badge), name, position badge, and OVR only — lets you pick a winning team and enter a price, and records SOLD or UNSOLD (each behind a confirm step) — team budgets, squad sizes, and progress update immediately, and the same in-progress auction is still there if you navigate to Teams or Players & Setup and back, or even close and reopen the app entirely. Only SOLD is permanent: once a round's queue is exhausted, any UNSOLD players are shuffled into a new "Re-Auction Round N" queue automatically, and AUCTION COMPLETE only appears once every auction-eligible player has been sold. Team buttons are disabled and show why (e.g. "FULL" at 8/8, "HAS GK" when the current player is a goalkeeper and the team already owns one, "LOW BUDGET" when a purchase would leave too little for the team's remaining required roster slots) whenever a team is not eligible to buy the current player, and each button shows its current legal maximum bid ("Max NNM") — all enforced in the service layer, not just the UI. If no eligible team remains for the players still unsold, the screen shows a blocked-state message instead of looping or falsely completing. Players & Setup is a real, read-only, searchable/filterable table of all 32 players plus a tournament setup-readiness check (including goalkeeper coverage). Player Cards is a separate, visual gallery of the same 32 players — a photo-forward card per player (position badge, OVR, last-season FPL points or "N/A", a CAPTAIN badge where relevant) with its own search box, an ALL/GK/DEF/MID/ATT/CAPTAINS filter, and OVR/FPL/name sorting; clicking a card opens a larger detail panel (full photo, all fields, plus the player's current auction status once a session is running) with a Back button — entirely read-only, like Players & Setup. Teams shows all four team cards (captain, budget, squad capacity, roster) — canonical data before an auction starts, live data once one is running. Auction History is a read-only, chronological transaction timeline for the current session — every SOLD/UNSOLD attempt in order (a player UNSOLD in an earlier round and later SOLD keeps both rows, never collapsed to one), with summary cards (Total Attempts, Sold, Unsold Attempts, Total Spent), search by name, filters (result/round/team), and an oldest/newest sort toggle. With no active session it shows a plain "NO ACTIVE AUCTION SESSION" message rather than starting one. Reports is the read-only auction summary/analytics dashboard for the current session — deliberately distinct from Auction History's event log: KPI cards (Total Sold, Total Spent, Avg/Median/Highest/Lowest Sale, Rounds), Most Expensive/Cheapest/Highest-Rated purchase highlights (with tie handling), FPL and OVR analytics (excluding players with no data from every average, never treating it as 0), a re-auction summary, team comparison bars (spending, remaining budget, squad size, average purchase price), and one panel per team (captain, budget, GK status, max next legal bid, and full roster with purchase prices — captains always shown as "CAPTAIN," never a "0M" purchase). Like Auction History, it shows a plain "NO ACTIVE AUCTION SESSION" message with no session active, and works identically whether the session is IN_PROGRESS, COMPLETE, or BLOCKED. Settings is the organizer configuration/diagnostics screen: tournament info, a Display/Projector panel (Fullscreen toggle — F11/Escape also work anywhere in the app — a "Remember Fullscreen" option, current resolution, and HDMI/projector connection instructions), Auction Preferences (Confirm SOLD/Confirm UNSOLD toggles, both ON by default — turning one off only skips that transaction's confirmation dialog, never any validation), a Reset App Preferences action (confirmation required; resets only these display/confirmation preferences, never auction data), Session/Save Status for whatever session is currently loaded (mode, id, status, round, SOLD count, last autosave, save location, and an Open Save Folder button), a Diagnostics panel (app/Python version, platform, player/team/photo counts, save schema version, and the same tournament setup-readiness check Players & Setup uses), a Captain Phone Bidding section with real START/STOP controls, the live LAN address, and a connected-device count, and a read-only "LOCKED TOURNAMENT RULES" summary — no core tournament rule (player/team counts, budget, squad size, GK rule, minimum price, budget reserve, re-auction behavior) is ever editable from this screen.

### Resuming a saved auction

Every auction autosaves to `saves/` after each SOLD, UNSOLD, or round transition — closing the app (or a crash) does not lose progress. To resume:

1. Launch `python main.py` — Live Auction opens on the session launcher automatically whenever no session is currently active in memory.
2. Under **LIVE**, click **Resume** to continue the tournament's one protected live session, or under **MOCKS**, click **Resume** next to whichever practice session you want to continue.
3. Play continues exactly where it left off — same round, same current player, same budgets/rosters/history.

A corrupt or unreadable save is labeled clearly ("SAVE CORRUPT / UNREADABLE") with only a Delete option — it's never silently loaded or auto-repaired. `saves/` is git-ignored; it is created automatically on first save and is never something you need to create by hand.

## Captain Phone Bidding — Local Test

Phase 1 lets one captain's phone place bids for their team over the same local Wi-Fi network as the organizer's laptop, while the organizer keeps full control from the desktop. This requires both devices on the **same Wi-Fi network** (a phone on cellular data will not be able to reach the laptop). PIN authentication for all four captains at once is a later phase — today, anyone who opens the page can pick any team from a plain selector, so only use this on a trusted network.

1. On the laptop, launch `python main.py` and start (or resume) a MOCK or LIVE auction from Live Auction as usual.
2. Open **Settings** and find the **Captain Phone Bidding** section.
3. Click **START CAPTAIN BIDDING**. The status changes to RUNNING and a real **LAN Address** appears (for example `http://192.168.1.23:8765`) — use **COPY ADDRESS** if you want to text or AirDrop it to the captain instead of reading it aloud.
4. **Windows Firewall may prompt the first time the server starts** — allow access on **Private networks only** (never Public). This app never changes firewall rules on its own; you must approve the prompt yourself.
5. On the captain's phone (connected to the same Wi-Fi), open a browser and go to the LAN address from step 3.
6. The phone shows "CALGARY VIPERS / CAPTAIN BIDDING" and "TEST MODE — NO TEAM AUTHENTICATION." Tap the captain's team (e.g. Blackout FC).
7. The phone now shows the current player (photo, name, position, OVR, FPL), the current highest bid and leading team, the captain's own team budget/squad/max legal bid, and +1M / +2M / +5M buttons.
8. Bids from the phone and bids from the laptop's own Live Auction screen (the new "PLACE BID" row, usable for any team — the organizer's fallback if only one phone is available today, or if the network fails) both update the same **CURRENT BID / LEADING TEAM** display on the laptop and on every connected phone within about half a second.
9. When ready, the organizer presses **SOLD** on the laptop as usual — if a phone bid is currently leading, its team and amount are pre-filled into the same confirmation dialog and the same transaction logic that has always handled SOLD/UNSOLD; nothing about how a sale is recorded changes. The bid display then resets to "—" / "—" for the next player, on both the laptop and every phone.
10. Click **STOP CAPTAIN BIDDING** in Settings when the trial (or the tournament) is over. This is safe to click even if the server was never started, and safe to start again later.

If phone bidding is unavailable for any reason (Wi-Fi trouble, no phone on hand, the server is OFF), the organizer can always finish the auction entirely from the laptop exactly as before — phone bidding is optional and never required to complete a sale.

## Development rules

- Read `PROJECT_CONTEXT.md` before changing the repository.
- Keep business logic independent from GUI callbacks.
- Treat `data/settings.json` as the source for configurable tournament values.
- Load the application name, branding name, header title, theme, and logo path from the `branding` settings object; do not hard-code them into future UI widgets.
- Keep Calgary Vipers separate from the four auction teams.
- Do not auction captains or reduce budgets for captain assignments.
- Update tests and `PROJECT_CONTEXT.md` whenever rules or implementation decisions change.
