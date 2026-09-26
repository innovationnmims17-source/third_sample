# Build a Portfolio, Survive the News — Live App

A one-file Streamlit app that runs the whole event: teams join on their own
phones/laptops, the controller (you) releases each news round when the room
is ready, and everyone watches the scoreboard update live.

## 1. Install

```bash
pip install -r requirements.txt
```

Just `streamlit` and `pandas` — no extra components needed.

## 2. Run it on the day

Run it on one laptop connected to the venue Wi-Fi, and have everyone else
join over that same network:

```bash
streamlit run app.py --server.address 0.0.0.0
```

Streamlit will print a "Network URL" like `http://192.168.1.23:8501` —
that's the link every team and you (the controller) open in a browser.
Project it / write it on a whiteboard at the start.

> All game state lives in the server's memory. If you restart the app,
> the game resets. Don't restart mid-event.

## 3. Before doors open

1. Open the link yourself, log in as **Controller** (PIN is `1234` —
   change `CONTROLLER_PIN` near the top of `app.py` before the event if
   you want a different one).
2. In the **Teams** tab, check/add the 4–5 team names you're using
   (defaults: The Bulls, The Hedgers, The Safe Hands, The Contrarians,
   The Analysts).
3. Rehearse a round if you like, then click **Start New Game** (on the
   Reveal screen, once revealed) or **Full Reset** (Teams tab) to wipe
   it before the real thing starts.

## 4. Running the event

- Teams open the link, pick **Join as a Team**, choose their team name.
  They split their ₹1,00,000: Nifty / Gold / Bitcoin / FD are set
  directly (₹10,000–₹50,000 each), and **Cash is always whatever's
  left over** — so the total can never be wrong, there's nothing to
  add up by hand.
- You watch the **Run the Game** tab: once enough teams have submitted,
  click **Close Allocation & Start Round 1**.
- There are **8 news rounds** (labelled 1–8 throughout, never 9 — "The
  Reveal" afterwards is its own separate stage). For each round: edit
  the headline if you want, hit **Release Headline to All Teams** — it
  appears on every team's screen automatically within a few seconds.
  Teams can rebalance **every round** (no card limit, no separate
  "use rebalance" toggle — the form is just always there), then lock
  in.
- Enter the market move (%) per asset — defaults are pre-filled but
  editable — and hit **Apply Round Results**. Every team's portfolio
  updates immediately, and the scoreboard shows who moved up/down in
  rank.
- Use the **Quiz Points** tab to award +1 for whoever buzzed in first.
- After Round 8, go to **The Reveal**, enter real price moves, and hit
  **Calculate Final Results & Reveal Winner**. The ₹10,000 quiz bonus
  is applied automatically, and every screen shows the final ranked
  scoreboard with confetti.
- To play again immediately: on the Reveal screen, tick the confirm
  box and hit **Start New Game** — same teams, fresh scores, no one
  has to log in again.

## What was fixed from the previous version

- **Bounds are flat ₹10,000–₹50,000**, not a 10%–50% percentage of a
  moving total (matches the printed rules exactly).
- **The total is now always exactly right, automatically.** Nifty,
  Gold, Bitcoin and FD are the only inputs; Cash is computed as
  whatever's left, live, as you type or drag. There's no "must add up
  to X" validation to fight with, and no way to mistype a total that
  doesn't match.
- **Switching between the slider and "type an exact amount" toggle no
  longer discards your edits** — both read from and write to the same
  underlying value, so whichever you touch last is what's kept.
- **Typing negative or out-of-range numbers is blocked at the input
  itself** (`min`/`max` are enforced on the number field, not just
  checked after the fact), so you can't end up with something like
  ‑221 in a field.
- **Auto-refresh no longer depends on an extra package that could fail
  silently.** Waiting screens (before a headline drops, after you've
  locked in, before the Reveal) now refresh themselves automatically
  every few seconds using nothing but the browser. Screens where you're
  actively entering numbers never auto-refresh, so you're never
  interrupted mid-edit. Login also now survives any refresh (manual or
  automatic) via the page URL, so nobody gets bounced back to the login
  screen.
- **Round numbering is consistent**: it's 8 news rounds, always shown
  as "Round X of 8" — "The Reveal" is its own stage afterward, not a
  9th round.

## Design notes

- **Fixed Deposit** defaults to +0.9% per news round (≈7% p.a. over 8
  rounds) and **Cash** defaults to 0%, both editable per round by the
  controller.
- The controller can apply a round's results even if not every team has
  locked in — any team that hasn't submitted just keeps compounding
  their last allocation, so the game never stalls on one slow team.
- Only one browser should be logged in as Controller at a time (there's
  no lock preventing two, so keep the PIN to yourself).
