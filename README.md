# Football Auction Manager

A desktop football-auction application for the Calgary Vipers 2026 tournament. The immediate goal is a reliable tournament-ready tool; the longer-term goal is a polished software portfolio project.

Calgary Vipers is the application and tournament branding identity. It is not an auction team. The official logo is stored at `assets/branding/calgary_vipers_logo.png`, and its UI-facing values are configurable in `data/settings.json`.

## Current state

Milestones 1–8 are complete: repository structure and domain models, a verified/finalized 32-player database and four-team initialization, a randomized hidden auction queue/order engine, a basic CustomTkinter application shell, a real, read-only Players & Setup screen, a real Teams screen (read-only for edits, but live-updating once an auction is running), a SOLD/UNSOLD auction transaction service, and a real, interactive **Live Auction** screen — start a session, reveal the current player, select a winning team, enter a price, confirm SOLD or UNSOLD, and watch team budgets/rosters/progress update immediately. Milestone 8.1 corrected the tournament rules on top of that: UNSOLD players automatically return in a re-auction round instead of ending the auction, a team may hold at most one goalkeeper and every final squad must contain exactly one, a team at 8/8 players is no longer eligible to bid, and a true dead-end (no eligible team can take the remaining players) surfaces as a clear "auction blocked" state instead of looping or silently completing. A pre-Milestone-9 fix then closed a budget gap: a team can no longer spend so much on one player that it can't afford the minimum price for every roster slot it still needs (later superseded by **First Auction Rules V2**, below). **Milestone 9 adds full persistence**: every auction is either a MOCK (practice) or LIVE (tournament) session, autosaves after every SOLD/UNSOLD/round-transition, and survives a full app restart exactly as it was. A September 2026 follow-up added **Player Cards** — a visual gallery/showcase of all 32 players (separate from the administrative Players & Setup table) with search/filter/sort and a click-to-expand detail view, plus a nullable last-season FPL points statistic per player. A further September 2026 follow-up turned **Auction History** into a real, read-only, chronological transaction-timeline screen for the current session (search/filter by result/round/team, summary cards, oldest/newest sort). A further September 2026 follow-up turned **Reports** into a real, read-only auction summary/analytics dashboard (KPIs, team comparisons, price/FPL/OVR analytics, re-auction summary). A further September 2026 follow-up turned **Settings** into a real organizer configuration/diagnostics screen (fullscreen/display, SOLD/UNSOLD confirmation preferences, session/save status, tournament diagnostics, and a locked, read-only tournament-rules summary). Every sidebar route now has a real screen. A September 2026 follow-up, **Captain Phone Bidding — Phase 1**, proved that a real phone on the same Wi-Fi network can place bids for one team while the organizer bids for the other teams from the laptop. A further September 2026 follow-up, **Captain Phone Bidding — Phase 2**, replaced that open team selector with real per-team PIN login and added full mixed-mode support: any number of captain phones from 0 to 4 may be connected at once, each locked to its own team, while the organizer can still bid manually for every team — connected, disconnected, or never logged in — from the laptop at all times. See "Captain Phone Bidding — Local Test" and "Four-Captain Setup" below. The organizer's laptop remains the sole authority for finalizing SOLD/UNSOLD, and the desktop-only workflow above still works unchanged with the phone-bidding server off. A further September 2026 follow-up, **Windows Packaging — RC0**, produced a test Windows `.exe` build (see "Windows Test Build" below) so the app can be launched without installing Python or using an editor — an RC0 test build only, not the final tournament release. A further September 2026 follow-up locked the **final 32-player tournament roster** (final replacements, positions, OVRs, photos, and confirmed FPL values). A further September 2026 follow-up, **First Auction Rules V2**, replaced the flat 1M minimum price / flat 1M-per-slot reserve with position-aware base prices (4M GK, 2M outfield) and a player-aware dynamic completion reserve — see "Tournament snapshot" above and PROJECT_CONTEXT.md's "FIRST AUCTION RULES V2" for the full rule and worked examples. A final September 2026 follow-up added **Match Results**, a new sidebar screen for post-auction match-score entry, automatic WIN/DRAW/LOSS money awards, and a transfer-budget tracker derived from each team's untouched first-auction remaining budget — see "How to Enter Match Results After the Auction" below. This is a funding tracker only; the second transfer window itself is not implemented yet.

## Tournament snapshot

- 32 total players: 4 pre-assigned captains and 28 auction-eligible players
- 4 teams, each beginning with a 100M auction budget
- 8-player final squad target: 1 captain plus up to 7 purchases, including exactly 1 goalkeeper
- Captains never enter the auction and cost nothing
- Positions: `GK`, `DEF`, `MID`, `ATT`
- **Base price** (First Auction Rules V2 — September 2026): 4M for a goalkeeper, 2M for every other position — the minimum any bid or sale price may be
- **Dynamic completion reserve**: a team may never spend so much on one purchase that it can no longer complete its remaining mandatory roster slots (including its still-needed goalkeeper) at their own minimum prices afterward — but a team *may* legitimately finish the auction with 0M remaining. See PROJECT_CONTEXT.md's "FIRST AUCTION RULES V2" for the full formula and worked examples.

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
├── requirements-build.txt        # Windows packaging (PyInstaller) -- build-time only
├── calgary_vipers_auction.spec   # PyInstaller build spec (see "Windows Test Build" below)
├── build_windows.bat             # One-command Windows .exe build
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

Running `python main.py` launches the Calgary Vipers Auction 2026 desktop window: a branded header and sidebar navigation (Live Auction, Player Cards, Players & Setup, Teams, Auction History, Reports, Settings). Live Auction (shown by default) opens on a **session launcher** when no auction is currently active: NEW MOCK AUCTION / NEW LIVE AUCTION buttons, plus a "Saved Sessions" panel listing the current LIVE session (if any) and every saved MOCK practice session with a Resume/Delete action each. Starting a new LIVE auction while one already exists asks for confirmation first — the old one is archived, never silently overwritten. Once a session is active, a compact "MOCK SESSION" / "LIVE SESSION" badge and an autosave-status indicator ("Autosaved HH:MM:SS" or a prominent "SAVE FAILED") sit next to the round/progress line, and the screen otherwise looks and works exactly as before: it reveals one randomized player at a time on a large sports-broadcast-style card — real photo (all 32 canonical players now have one; a missing/unreadable photo falls back to an initials badge), name, position badge, and OVR only — lets you pick a winning team and enter a price, and records SOLD or UNSOLD (each behind a confirm step) — team budgets, squad sizes, and progress update immediately, and the same in-progress auction is still there if you navigate to Teams or Players & Setup and back, or even close and reopen the app entirely. Only SOLD is permanent: once a round's queue is exhausted, any UNSOLD players are shuffled into a new "Re-Auction Round N" queue automatically, and AUCTION COMPLETE only appears once every auction-eligible player has been sold. Team buttons are disabled and show why (e.g. "FULL" at 8/8, "HAS GK" when the current player is a goalkeeper and the team already owns one, "NEEDS GK" when this purchase would be the team's last roster slot without ever having acquired its mandatory goalkeeper, "LOW BUDGET" when a purchase would leave too little for the team's remaining required roster slots at their own minimum prices) whenever a team is not eligible to buy the current player, and each button shows its current player-aware legal maximum bid ("Max NNM") — all enforced in the service layer, not just the UI. The CURRENT BID row also shows the current player's own **BASE PRICE** (4M for a goalkeeper, 2M otherwise) — no bid or sale below it is ever accepted. If no eligible team remains for the players still unsold, the screen shows a blocked-state message instead of looping or falsely completing. Players & Setup is a real, read-only, searchable/filterable table of all 32 players plus a tournament setup-readiness check (including goalkeeper coverage). Player Cards is a separate, visual gallery of the same 32 players — a photo-forward card per player (position badge, OVR, last-season FPL points or "N/A", a CAPTAIN badge where relevant) with its own search box, an ALL/GK/DEF/MID/ATT/CAPTAINS filter, and OVR/FPL/name sorting; clicking a card opens a larger detail panel (full photo, all fields, plus the player's current auction status once a session is running) with a Back button — entirely read-only, like Players & Setup. Teams shows all four team cards (captain, budget, squad capacity, roster) — canonical data before an auction starts, live data once one is running. Auction History is a read-only, chronological transaction timeline for the current session — every SOLD/UNSOLD attempt in order (a player UNSOLD in an earlier round and later SOLD keeps both rows, never collapsed to one), with summary cards (Total Attempts, Sold, Unsold Attempts, Total Spent), search by name, filters (result/round/team), and an oldest/newest sort toggle. With no active session it shows a plain "NO ACTIVE AUCTION SESSION" message rather than starting one. Reports is the read-only auction summary/analytics dashboard for the current session — deliberately distinct from Auction History's event log: KPI cards (Total Sold, Total Spent, Avg/Median/Highest/Lowest Sale, Rounds), Most Expensive/Cheapest/Highest-Rated purchase highlights (with tie handling), FPL and OVR analytics (excluding players with no data from every average, never treating it as 0), a re-auction summary, team comparison bars (spending, remaining budget, squad size, average purchase price), and one panel per team (captain, budget, GK status, max next legal bid, and full roster with purchase prices — captains always shown as "CAPTAIN," never a "0M" purchase). Like Auction History, it shows a plain "NO ACTIVE AUCTION SESSION" message with no session active, and works identically whether the session is IN_PROGRESS, COMPLETE, or BLOCKED. Settings is the organizer configuration/diagnostics screen: tournament info, a Display/Projector panel (Fullscreen toggle — F11/Escape also work anywhere in the app — a "Remember Fullscreen" option, current resolution, and HDMI/projector connection instructions), Auction Preferences (Confirm SOLD/Confirm UNSOLD toggles, both ON by default — turning one off only skips that transaction's confirmation dialog, never any validation), a Reset App Preferences action (confirmation required; resets only these display/confirmation preferences, never auction data), Session/Save Status for whatever session is currently loaded (mode, id, status, round, SOLD count, last autosave, save location, and an Open Save Folder button), a Diagnostics panel (app/Python version, platform, player/team/photo counts, save schema version, and the same tournament setup-readiness check Players & Setup uses), a Captain Phone Bidding section with real START/STOP controls, the live LAN address, each team's PIN (organizer-only), and a per-team CONNECTED/DISCONNECTED/NOT CONNECTED status with a RESET CONNECTION button and a REGENERATE ALL PINS action, and a read-only "LOCKED TOURNAMENT RULES" summary — no core tournament rule (player/team counts, budget, squad size, GK rule, base prices, dynamic completion reserve, re-auction behavior) is ever editable from this screen.

### Resuming a saved auction

Every auction autosaves to `saves/` after each SOLD, UNSOLD, or round transition — closing the app (or a crash) does not lose progress. To resume:

1. Launch `python main.py` — Live Auction opens on the session launcher automatically whenever no session is currently active in memory.
2. Under **LIVE**, click **Resume** to continue the tournament's one protected live session, or under **MOCKS**, click **Resume** next to whichever practice session you want to continue.
3. Play continues exactly where it left off — same round, same current player, same budgets/rosters/history.

A corrupt or unreadable save is labeled clearly ("SAVE CORRUPT / UNREADABLE") with only a Delete option — it's never silently loaded or auto-repaired. `saves/` is git-ignored; it is created automatically on first save and is never something you need to create by hand.

## Captain Phone Bidding — Local Test

One captain's phone can place bids for their team over the same local Wi-Fi network as the organizer's laptop, while the organizer keeps full manual control from the desktop at all times. This requires both devices on the **same Wi-Fi network** (a phone on cellular data will not be able to reach the laptop). Each team logs in with its own 4-digit PIN — see "Four-Captain Setup" below for the full multi-captain flow; this section walks through a single phone first.

1. On the laptop, launch `python main.py` and start (or resume) a MOCK or LIVE auction from Live Auction as usual.
2. Open **Settings** and find the **Captain Phone Bidding** section.
3. Click **START CAPTAIN BIDDING**. The status changes to RUNNING and a real **LAN Address** appears (for example `http://192.168.1.23:8765`) — use **COPY ADDRESS** if you want to text or AirDrop it to the captain instead of reading it aloud.
4. **Windows Firewall may prompt the first time the server starts** — allow access on **Private networks only** (never Public). This app never changes firewall rules on its own; you must approve the prompt yourself.
5. In the **CAPTAIN PINS / CONNECTIONS** list further down the same section, note the 4-digit PIN next to the team you're testing (e.g. Blackout FC) — use **COPY** if you want to send it to the captain rather than reading it aloud. These PINs are organizer-only and never shown anywhere else in the app.
6. On the captain's phone (connected to the same Wi-Fi), open a browser and go to the LAN address from step 3.
7. The phone shows "CALGARY VIPERS / CAPTAIN BIDDING" and "ENTER TEAM PIN." Enter the 4-digit PIN and tap **CONNECT**. The phone now shows "CONNECTED AS BLACKOUT FC" (or whichever team the PIN belongs to) — there is no team selector; the PIN alone determines identity, and the phone can bid only for that team.
8. Back in Settings, that team's row in **CAPTAIN PINS / CONNECTIONS** now shows **CONNECTED**, and the Live Auction screen's team button for that team shows a small "PHONE CONNECTED" note.
9. The phone now shows the current player (photo, name, position, OVR, FPL, and its own BASE PRICE — 4M for a goalkeeper, 2M otherwise), the current highest bid and leading team, the captain's own team budget/squad/max legal bid, and +1M / +2M / +5M buttons — before any bid exists, a tap submits `max(base_price, increment)`, never a bid below the player's own minimum.
10. Bids from the phone and bids from the laptop's own Live Auction screen (the "PLACE BID" row, usable for **any** team at any time — connected, disconnected, or never logged in) both update the same **CURRENT BID / LEADING TEAM** display on the laptop and on every connected phone within about a second.
11. When ready, the organizer presses **SOLD** on the laptop as usual — if a phone bid is currently leading, its team and amount are pre-filled into the same confirmation dialog and the same transaction logic that has always handled SOLD/UNSOLD; nothing about how a sale is recorded changes. The bid display then resets to "—" / "—" for the next player, on both the laptop and every phone.
12. Click **STOP CAPTAIN BIDDING** in Settings when the trial (or the tournament) is over. This is safe to click even if the server was never started, and safe to start again later — connected captains simply keep their login and reconnect automatically.

If phone bidding is unavailable for any reason (Wi-Fi trouble, no phone on hand, the server is OFF, a captain's PIN input is fumbled), the organizer can always finish the auction entirely from the laptop exactly as before — phone bidding is optional and never required to complete a sale.

## Four-Captain Setup

The full tournament design supports all four captains bidding from their own phones at once, with the organizer still able to step in manually for any team at any time.

1. Make sure the laptop and every captain's phone are on the **same Wi-Fi network**.
2. On the laptop, open **Settings** and click **START CAPTAIN BIDDING**.
3. In the **CAPTAIN PINS / CONNECTIONS** list, privately give each captain their team's own 4-digit PIN — in person or via a private message, not read aloud where an opposing captain could hear it.
4. Each captain opens the LAN address shown in Settings on their own phone and enters their PIN. Once connected, a phone is locked to that one team for the rest of the session — it cannot switch to another team without the organizer resetting that connection first.
5. Connection status for all four teams appears live in Settings (CONNECTED / DISCONNECTED / NOT CONNECTED) — refresh by simply looking at the screen, no action needed. The Live Auction projector view shows a compact "N / 4 ACTIVE" count so the organizer always knows how many captains are currently bidding from their phones without needing to open Settings.
6. **Manual bidding for every team remains available on the laptop at all times**, regardless of how many phones are connected — 0, 1, 2, 3, or all 4. A captain losing Wi-Fi mid-auction is not an emergency: their team simply shows DISCONNECTED, and the organizer can keep bidding on their behalf from the laptop until they reconnect.
7. If a captain switches phones, or their session needs to be freed up for any reason, the organizer clicks **RESET CONNECTION** next to that team in Settings — this only affects that team's login, never the roster, budget, history, or current live bid. The captain can then log back in with the same PIN on the new device.
8. If a PIN is ever compromised or the organizer wants a clean slate before the tournament, **REGENERATE ALL PINS** issues a brand-new PIN for every team and signs out every currently connected captain (confirmation required) — auction data is never affected.
9. The organizer alone presses **SOLD** and **UNSOLD** on the laptop for every transaction, regardless of how many phones are connected — captain phones can only submit bids, never finalize a sale.

## How to Enter Match Results After the Auction

A September 2026 follow-up added **Match Results**, a new sidebar screen for the post-auction phase: the organizer enters each tournament match's final score, the app automatically awards match money (WIN 4M / DRAW 2M / LOSS 1M), and each team's current transfer budget (first-auction remaining + total match earnings) updates immediately. This is a funding tracker only — the second transfer window itself is not implemented yet.

1. **Complete the first auction.** Match Results stays visible the whole time, but shows "FIRST AUCTION IN PROGRESS" (or "FIRST AUCTION BLOCKED" if the auction ever becomes blocked) until the auction's status is actually COMPLETE — official match-money accounting is locked until then.
2. **Open the completed tournament record.** If you're continuing straight from a just-finished auction, it's already open. If you closed the app, reopen it, go to Live Auction, and click **Resume** next to the completed session (LIVE or MOCK) in the "Saved Sessions" panel — this reopens it in the same safe, read-only auction view as always (no SOLD/UNSOLD/bidding controls); resuming a COMPLETE session never restarts or replays it.
3. **Navigate to Match Results** in the sidebar.
4. **Enter scores**: pick the two different teams, type each team's goals, and click **SAVE RESULT**. The app shows the winner (or DRAW) and each team's award immediately in the Saved Match History table below.
5. **Check the automatically updated budgets** in the Transfer Budget Tracker section — each team's card shows its First Auction Remaining, Match Earnings, and AVAILABLE TRANSFER BUDGET.
6. **Made a mistake?** Click **EDIT** on that match's row, correct the score, and click **UPDATE RESULT** — the old award is replaced, never added to. Click **DELETE** (confirmation required) to remove a match entirely; its award disappears from both teams' totals immediately. The original first-auction remaining budget is never affected by any of this.
7. **Close/reopen safely**: every SAVE/EDIT/DELETE autosaves immediately, exactly like SOLD/UNSOLD during the auction itself. Closing and reopening the app and resuming the same session shows every match result exactly as you left it.
8. **Continue entering results later**, any time, across as many sessions as the tournament needs — nothing here needs to happen in one sitting.

## Windows Test Build

**This is an RC2 test build to verify packaging before final roster lock — it is not the final v1.0 tournament release.** It lets the app run on the tournament laptop without installing Python or using an editor/terminal. RC1 fixed a packaged-only bug found in RC0 where Captain Phone Bidding's server could hang after the organizer had already used the app for a bit, but it still hung on the organizer's own laptop; RC2 adds a hard startup timeout so the server can never again get stuck showing STARTING forever — after 20 seconds it fails clearly with a specific reason instead, and a retry (or the console-attached RC2 Debug build) can be used to diagnose further (see "RC1 Real-Laptop Server Start Hang — Targeted Debug Pass" in `PROJECT_CONTEXT.md`). RC0 and RC1 are kept alongside RC2 under `dist/` for comparison, but RC2 is what you should actually use.

### Building it

```
python -m pip install -r requirements-build.txt
build_windows.bat
```

This produces:

```
dist/Calgary Vipers Auction 2026 RC2/
    Calgary Vipers Auction 2026.exe
    _internal/            (bundled Python, libraries, assets/, data/)
```

`build_windows.bat` removes only *this RC's own* `build/` and `dist/Calgary Vipers Auction 2026 RC2/` (or `dist/Calgary Vipers Auction 2026 RC2 Debug/`) folder before rebuilding — it never touches an earlier RC's folder under `dist/` (e.g. RC0, RC1), and never touches `saves/`, `config/`, or any canonical data, all of which live at the repository root. Set `CVA_DEBUG_CONSOLE=1` before running the script to instead produce a console-attached debug variant, output to `dist/Calgary Vipers Auction 2026 RC2 Debug/` — its console window (and the log file) show the fine-grained `SERVER_START_XX`/`SERVER_THREAD_XX`/`WATCHER_XX`/`LAN_XX` startup trace, useful if Captain Bidding ever fails to start on a specific machine. The normal build is windowed with no visible console, output to `dist/Calgary Vipers Auction 2026 RC2/`, and is what you should hand to the organizer.

### Launching it

Double-click `Calgary Vipers Auction 2026.exe` inside the `dist\Calgary Vipers Auction 2026 RC2\` folder. No Python installation, VS Code, or terminal command is needed on the machine that runs it — only on the machine that builds it.

### Writable data (config/, saves/, logs/)

The packaged app creates `config/`, `saves/`, and `logs/` **beside the .exe** the first time it needs them (never inside the read-only `_internal/` folder) — this is where PIN configuration (`config/captain_bidding.json`), app preferences (`config/user_preferences.json`), MOCK/LIVE auction saves (`saves/`), and a small diagnostic log (`logs/calgary_vipers_auction.log`) live for the packaged build, exactly mirroring source-mode behavior. They persist across closing and reopening the .exe, and an existing save or PIN file is never overwritten on relaunch. To test: create a MOCK session, close the app, reopen the .exe, and resume — the session should be exactly where you left it. The log file never contains a PIN or a captain's auth token; open it in Notepad any time to see recent app startup / captain-bidding server activity without needing a debug build.

### Captain Phone Bidding from the packaged build

Works identically to running from source: open Settings, click **START CAPTAIN BIDDING**. The status badge shows STARTING briefly, then RUNNING with a real LAN address — the window stays fully responsive the whole time. If startup ever gets stuck, it now **cannot stay STARTING forever**: after 20 seconds it shows **"SERVER START FAILED"** with the specific stage it stopped at — click START again (waiting a few seconds after a FAILED result before retrying is safest). If it still won't start after a retry, run the `RC2 Debug` build instead: its console window (and `logs/calgary_vipers_auction.log`) show exactly which startup stage it never got past, which pinpoints the problem for further diagnosis. Then follow the same steps as "Captain Phone Bidding — Local Test" / "Four-Captain Setup" above. **Windows Firewall may prompt the first time the packaged .exe starts the server** — allow access on **Private networks only** (never Public); this app never changes firewall rules on its own. If the server shows RUNNING but a phone still can't connect, this Firewall prompt (or having missed it) is the most likely cause.

### Known limitations of this RC2 build

- No application icon yet (no suitable `.ico` exists — a missing icon is preferred over converting one poorly for this build).
- Not code-signed — Windows SmartScreen may show an "unrecognized app" warning on first run, same as any unsigned executable; choose "More info" → "Run anyway" if you built it yourself.
- Only tested so far on the machine it was built on — test it on the actual tournament laptop before relying on it on tournament day.

## Development rules

- Read `PROJECT_CONTEXT.md` before changing the repository.
- Keep business logic independent from GUI callbacks.
- Treat `data/settings.json` as the source for configurable tournament values.
- Load the application name, branding name, header title, theme, and logo path from the `branding` settings object; do not hard-code them into future UI widgets.
- Keep Calgary Vipers separate from the four auction teams.
- Do not auction captains or reduce budgets for captain assignments.
- Update tests and `PROJECT_CONTEXT.md` whenever rules or implementation decisions change.
