"""FUT Gallery Tokens bot - EasySBC token plan -> buy the missing cards on the EA web app -> relist them.

  python run.py             EVERYTHING (what GalleryBot.bat runs): log in to both sites -> read your coins -> import
                            your club -> re-solve the Gallery sets (1 EasySBC AI credit) -> token plan for your coins
                            -> you approve (YES) -> buy + relist -> list of sets to complete in game + expected tokens
  python run.py --dry-run   the same, but only checks live market prices - buys nothing
  python run.py plan        build and show the token plan, no buying
  python run.py login       open the bot's Chrome window to log in to easysbc.io / the EA web app yourself
  python run.py relist      list any bot-bought cards still sitting unlisted on your transfer list
  python run.py test-buy    buy + relist one named card (smoke test of the web app part)
  python run.py levels      the older Gallery LEVEL (score) flow from easysbc.io/fut-gallery/levels"""
import argparse
import csv
import datetime as dt
import json
import random
import re
import sys
import time

from gallerybot.browser import Browser, ROOT
from gallerybot.easysbc import LEVELS_URL, build_plan, read_plan, status
from gallerybot.tokens import GALLERY_URL, account, import_club, mark_completed, read_token_plan, update_solutions
from gallerybot.webapp import WEBAPP_URL, StopBot, WebApp

LOGS = ROOT / "logs"
STATE = LOGS / "state.json"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
sys.stdout.reconfigure(encoding="utf-8")


# ---------------- helpers ----------------
def snap_up(p):
    """Round up to a valid EA price step."""
    for limit, step in ((1000, 50), (10000, 100), (50000, 250), (100000, 500), (10**9, 1000)):
        if p <= limit:
            return int(-(-p // step) * step)


def cap_for(card):
    base = card["price"] or 0
    if not base:
        return 0
    cap = max(base * (1 + CFG["price_cap_margin_pct"] / 100), base + CFG["price_cap_min_extra"])
    return min(snap_up(cap), CFG["absolute_max_per_card"])


def load_state():
    s = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    s.setdefault("bought", {})
    s.setdefault("pending_sets", {})
    return s


def save_state(s):
    LOGS.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def log_row(row):
    LOGS.mkdir(exist_ok=True)
    f = LOGS / f"buys_{dt.date.today():%Y%m%d}.csv"
    new = not f.exists()
    with f.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time", "set", "name", "rating", "defId", "easysbc_price", "cap", "paid", "note"])
        w.writerow(row)


def ask(msg, default=""):
    v = input(msg).strip()
    return v or default


def session_budget(coins):
    return max(0, min(CFG["max_coins_per_session"], coins - CFG["coin_reserve"]))


def relister(wa):
    """Collects bought cards and lists them in batches at the price paid."""
    pending = {}

    def add(def_id, price):
        pending[def_id] = price
        if len(pending) >= CFG["batch_size"]:
            flush()

    def flush():
        if not pending:
            return
        try:
            for name, d, binp, ok in wa.relist(dict(pending), CFG["listing_duration_s"]):
                print(f"   listed {name} at {binp:,}: {'ok' if ok else 'FAILED'}", flush=True)
        except Exception as e:
            print(f"   ! relist failed ({e.__class__.__name__}) - run 'python run.py relist' later", flush=True)
        pending.clear()
    return add, flush


def buy_one(wa, c, state, dry):
    """Search (dry) or buy one card. Returns the price paid / cheapest seen, or None."""
    cap = cap_for(c)
    if dry:
        rows, err = wa.search(c, cap)
        if not err:
            wa.back()                            # leave the results page
        cheapest = min(b for _, b in rows) if rows else None
        print(f"   [dry] {c['name']} {c['rating']}: "
              f"{err or (f'cheapest {cheapest:,} (cap {cap:,})' if cheapest else f'none under cap {cap:,}')}", flush=True)
        wa.pause()
        return cheapest
    price, note = wa.buy_card(c, cap)
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    log_row([stamp, c["set"], c["name"], c["rating"], c["resourceId"], c["price"], cap, price or "", note])
    print(f"   {c['name']} {c['rating']}: {('BOUGHT ' + format(price, ',')) if price else 'not bought'} - {note}", flush=True)
    if price:
        state["bought"][str(c["resourceId"])] = {"price": price, "set": c["set"], "at": stamp}
        save_state(state)
    time.sleep(random.uniform(*CFG["delay_between_buys_s"]))
    return price


# ---------------- token plan ----------------
def why_not(c):
    """Why the bot cannot buy this card ('' = it can)."""
    src = c.get("source") or "market"
    if src == "objective":
        return "objective reward - do the objective"
    if src == "tokens":
        return f"Gallery token store, {c['price']} tokens"
    if src != "market":
        return f"{src} card"
    if not c["price"]:
        return "no market price on EasySBC"
    if c["price"] > CFG["absolute_max_per_card"]:
        return f"{c['price']:,} > absolute_max_per_card"
    return ""


def classify(tp, state):
    """Split the plan's sets: free (nothing to buy), blocked (needs a card that is not on the market or over the
    per-card max), buy (ordered by tokens per coin). Cards bought by an earlier run count as owned."""
    free, blocked, buy = [], [], []
    for s in tp["sets"]:
        need = [c for c in s["buy"] if str(c["resourceId"]) not in state["bought"]]
        bad = [c for c in need if why_not(c)]
        s = {**s, "need": need, "cost": sum(c["price"] for c in need)}
        (free if not need else blocked if bad else buy).append(s)
        if bad:
            s["why"] = ", ".join(f"{c['name']} {c['rating']} ({why_not(c)})" for c in bad)
    buy.sort(key=lambda s: s["tokens"] / max(s["cost"], 1), reverse=True)
    return free, blocked, buy


def pick_run(buy, coins):
    """The whole sets (half a set gives no tokens) with the most tokens that fit this run's card limit and coin
    budget (worst-case prices) - a small knapsack over (cards, coins)."""
    budget, limit = session_budget(coins), CFG["max_buys_per_session"]
    dp = {0: [(0, 0, ())]}                       # cards used -> non-dominated (cost, tokens, set indexes)
    for i, s in enumerate(buy):
        k, cost, tok = len(s["need"]), sum(cap_for(c) for c in s["need"]), s["tokens"]
        for used, opts in list(dp.items()):
            if used + k > limit:
                continue
            for c0, t0, ids in opts:
                if c0 + cost <= budget:
                    dp.setdefault(used + k, []).append((c0 + cost, t0 + tok, ids + (i,)))
        for used, opts in dp.items():           # keep only the Pareto front per card count
            opts.sort(key=lambda o: (o[0], -o[1]))
            front, best = [], -1
            for o in opts:
                if o[1] > best:
                    front.append(o)
                    best = o[1]
            dp[used] = front
    best = max((o for opts in dp.values() for o in opts), key=lambda o: (o[1], -o[0]))
    return [buy[i] for i in best[2]], budget


def show_token_plan(tp, acc, state, coins):
    t = tp["totals"]
    free, blocked, buy = classify(tp, state)
    gross = sum({c["resourceId"]: c["price"] for s in buy for c in s["need"]}.values())
    print(f"\n=== EasySBC token plan for {tp['budget']:,} coins ===")
    print(f"  +{t.get('tokens', 0):,} tokens from {t.get('sets', 0)} sets | {t.get('transfers', 0):,} cards to buy | "
          f"tax loss ~{t.get('taxLoss', 0):,} (the real cost) | EasySBC 'coins needed' {t.get('coinsNeeded', 0):,}")
    print(f"  Set by set, the cards cost {gross:,} up front. You get most of that back when they sell, so the plan runs"
          f" in rounds: buy what your coins allow, list, run again after they sell.")
    if tp["missing"]:
        print("  ! could not read:", ", ".join(tp["missing"]))
    n, numbered = 0, {}
    if free:
        print(f"\nFREE - you already own the cards, just complete these in game ({sum(s['tokens'] for s in free)} tokens):")
        for s in free:
            n += 1
            numbered[n] = s
            print(f"  {n:>2}. {s['name'][:28]:28} {s['done'] or '-':>2} -> {s['grade']}  +{s['tokens']:>3} tokens")
    if blocked:
        print("\nBLOCKED - needs a card the bot cannot buy:")
        for s in blocked:
            n += 1
            numbered[n] = s
            print(f"  {n:>2}. {s['name'][:28]:28} {s['done'] or '-':>2} -> {s['grade']}  +{s['tokens']:>3} tokens  needs {s['why']}")
    run, budget = pick_run(buy, coins)
    run_ids = {s["id"] for s in run}
    if buy:
        print("\nTO BUY - most tokens per coin first (* = in this run):")
        for s in buy:
            n += 1
            numbered[n] = s
            print(f" {'*' if s['id'] in run_ids else ' '}{n:>2}. {s['name'][:28]:28} {s['done'] or '-':>2} -> {s['grade']}"
                  f"  +{s['tokens']:>3} tokens  {len(s['need']):>2} cards  ~{s['cost']:>6,} coins")
    run_cards = len({c["resourceId"] for s in run for c in s["need"]})
    run_cost = sum({c["resourceId"]: c["price"] for s in run for c in s["need"]}.values())
    free_tok, run_tok, plan_tok = sum(s["tokens"] for s in free), sum(s["tokens"] for s in run), t.get("tokens", 0)
    print(f"\nTHIS RUN (budget {budget:,} of your {coins:,} coins, max {CFG['max_buys_per_session']} cards): "
          f"{len(run)} sets, {run_cards} cards, ~{run_cost:,} coins up front, tax ~{run_cost // 20:,}.")
    base = acc.get("tokens")
    print(f"EXPECTED TOKENS: free sets +{free_tok}, this run +{run_tok} -> +{free_tok + run_tok} after you complete them"
          f" in game; whole plan +{plan_tok}." + (f" (EasySBC counts {base:,} tokens so far -> {base + free_tok + run_tok:,}"
                                                  f" after this run, {base + plan_tok:,} after the whole plan.)" if base is not None else ""))
    return numbered, free, run


def parse_marks(txt, numbered):
    """'4=B 9=S' -> {setId: grade}."""
    out = {}
    for num, g in re.findall(r"(\d+)\s*=\s*([DCBASdcbas])", txt):
        s = numbered.get(int(num))
        if s:
            out[s["id"]] = g.upper()
    return out


def run_sets(wa, run, state, dry):
    """Buy every missing card of each set in the run; relist in batches. Returns (spent, bought, complete sets)."""
    budget = session_budget(wa.coins() or 0)
    add, flush = relister(wa)
    spent, n, complete = 0, 0, []
    wa.open_search()
    try:
        for s in run:
            print(f"\n{s['name']} -> grade {s['grade']} (+{s['tokens']} tokens): {len(s['need'])} cards", flush=True)
            ok = True
            for c in s["need"]:
                if str(c["resourceId"]) in state["bought"]:
                    continue                     # bought earlier in this run for another set
                if n >= CFG["max_buys_per_session"] or spent + cap_for(c) > budget:
                    print("   run limit reached - this set stays incomplete", flush=True)
                    ok = False
                    break
                price = buy_one(wa, c, state, dry)
                if not price:
                    ok = False
                    continue
                spent, n = spent + price, n + 1
                if dry:
                    state["bought"][str(c["resourceId"])] = {"price": price, "dry": True}
                else:
                    add(c["resourceId"], price)
            if ok:
                complete.append(s)
            if n >= CFG["max_buys_per_session"]:
                break
    except StopBot as e:
        print(f"\nSTOPPED: {e}")
    finally:
        if not dry:
            flush()                              # whatever stopped the run, list what it bought
    return spent, n, complete


def connect_webapp(br):
    wa = WebApp(br.page(WEBAPP_URL, newest=True), CFG, log_row)
    return wa, wa.ensure_ready(interactive=True)


# ---------------- commands ----------------
def cmd_full(br, dry, plan_only=False):
    print("FUT Gallery Tokens bot. Close FC 27 / the companion app first - EA allows one session at a time.\n")
    # 1) both sites open in the bot window; you log in yourself
    es = br.page(GALLERY_URL)
    br.page(WEBAPP_URL)
    acc = account(es)
    if not acc["logged_in"]:
        ask("EasySBC: log in on the EasySBC tab of the bot window, then press Enter... ")
        acc = account(es)

    # 2) web app + coins
    wa, coins = connect_webapp(br)
    print(f"EA web app: logged in, {coins:,} coins.")

    # 3) import the club (EasySBC's extension, in this window so the web app is not logged in twice)
    print(f"EasySBC: club imported = {acc['imported']}, last import = {acc['last_import'] or 'never'}")
    if (not acc["imported"] or ask("Import your club now (recommended)? (Y/n): ", "y").lower() == "y") \
            and br.ensure_extension():
        es = br.page(GALLERY_URL)
        es.bring_to_front()
        print("Importing your club (EasySBC's extension opens the EA web app in this window)...", flush=True)
        if not import_club(es):
            ask("The import has not finished. Check the EasySBC / web app tabs, then press Enter to continue... ")
        acc = account(es)
        print(f"EasySBC: last import = {acc['last_import'] or 'unknown'}")
        wa, coins = connect_webapp(br)           # the import may have opened its own web app tab
        print(f"EA web app: {coins:,} coins.")

    # 4) sets the last run bought for - completed in game since?
    state = load_state()
    pend = state["pending_sets"]
    if pend:
        print("\nLast run bought everything for: " + ", ".join(f"{v['name']} ({v['grade']})" for v in pend.values()))
        if ask("Have you completed them in game at those grades? (y/N): ").lower() == "y":
            mark_completed(es, {int(k): v["grade"] for k, v in pend.items()})
            state["pending_sets"] = {}
            save_state(state)
            print("Marked on EasySBC.")

    # 5) re-solve every set for the club as it is now (1 AI credit)
    cr = acc["credits"]
    reset = dt.datetime.fromtimestamp(acc["credits_reset"]).strftime("%H:%M") if acc.get("credits_reset") else "?"
    if cr == 0:
        print(f"\nNo EasySBC AI credits left today (next at {reset}) - using the solutions EasySBC already has.")
    elif ask(f"\nRe-solve all Gallery sets for your club now? Uses 1 EasySBC AI credit"
             f"{f' ({cr} left today)' if cr is not None else ''}. (Y/n): ", "y").lower() == "y":
        print("Solving (a few minutes)...", flush=True)
        print("Solutions updated." if update_solutions(es) else "Could not re-solve - using the existing solutions.")

    # 6) token plan for your coins; fix sets EasySBC thinks are not done; repeat until right
    budget = max(0, coins - CFG["coin_reserve"])
    while True:
        print(f"\nBuilding the token plan for {budget:,} coins and reading every set's cards...", flush=True)
        tp = read_token_plan(es, budget)
        LOGS.mkdir(exist_ok=True)
        (LOGS / "last_token_plan.json").write_text(json.dumps(tp, indent=1), encoding="utf-8")
        numbered, free, run = show_token_plan(tp, acc, state, coins)
        fix = ask("\nAny of these sets ALREADY at that grade or higher in game? Type number=grade (e.g. 4=B 9=S),"
                  " or Enter if the list is right: ")
        marks = parse_marks(fix, numbered)
        if not marks:
            break
        mark_completed(es, marks)
        print(f"Marked {len(marks)} set(s) on EasySBC - re-planning.")
    if plan_only:
        return
    if not run:
        print("\nNothing to buy in this run." + (" Complete the FREE sets in game." if free else ""))
        return

    # 7) approval
    if dry:
        print("\n--dry-run: searching live prices for this run's cards, buying nothing.")
    elif ask("\nApprove: buy these cards and list them at the price paid? Type YES: ") != "YES":
        print("Not approved - nothing bought.")
        return
    wa, coins = connect_webapp(br)

    # 8) buy + relist
    spent, n, complete = run_sets(wa, run, state, dry)
    if dry:
        for k in [k for k, v in state["bought"].items() if v.get("dry")]:
            del state["bought"][k]
        print(f"\n=== Dry run done: {n} cards found under their caps (~{spent:,} coins); "
              f"{len(complete)}/{len(run)} sets fully available. Nothing bought. ===")
        return
    for s in complete:
        state["pending_sets"][str(s["id"])] = {"name": s["name"], "grade": s["grade"], "tokens": s["tokens"]}
    save_state(state)
    gain = sum(s["tokens"] for s in free) + sum(s["tokens"] for s in complete)
    print(f"\n=== Done: bought {n} cards for {spent:,} coins, all listed at the price paid. ===")
    print("NOW IN GAME: open each set, press Optimize (or Autocomplete) and submit at the grade shown:")
    for s in free + complete:
        print(f"  {s['name']} -> {s['grade']}  (+{s['tokens']} tokens)")
    if len(complete) < len(run):
        print("Not complete yet (a card could not be bought): "
              + ", ".join(s["name"] for s in run if s not in complete))
    base = acc.get("tokens")
    print(f"EXPECTED: +{gain} tokens once those are submitted"
          + (f" (EasySBC count {base:,} -> {base + gain:,})." if base is not None else "."))
    print("Run the bot again after the listed cards sell - it continues with the next sets.")


def cmd_levels(br, dry):
    """The older Gallery LEVEL flow (score, not tokens)."""
    es = br.page(LEVELS_URL)
    st = status(es)
    if not st["logged_in"]:
        ask("\nEasySBC is not logged in. Log in on the EasySBC tab of the bot window, then press Enter... ")
    wa, coins = connect_webapp(br)
    print(f"Web app logged in. Coins: {coins:,}")
    es = br.page(LEVELS_URL)
    lvl = ask("\nTarget Gallery level (10/15/20/25) - Enter = keep the plan already on EasySBC: ")
    if lvl:
        print(f"Building the Level {lvl} plan on EasySBC (uses 1 AI credit)...", flush=True)
        if not build_plan(es, int(lvl), max(0, coins - CFG["coin_reserve"])):
            print("The plan did not finish building - using whatever plan is on the page.")
    plan = read_plan(es)
    state = load_state()
    todo = sorted((c for c in plan["buy"] if str(c["resourceId"]) not in state["bought"]
                   and 0 < c["price"] <= CFG["absolute_max_per_card"]), key=lambda c: c["score"] / c["price"], reverse=True)
    t = plan["totals"] or {}
    print(f"\n=== Level {plan['level']} plan: score {t.get('score', 0):,}, {len(todo)} affordable cards, best score per coin first ===")
    for c in todo[:30]:
        print(f"  {c['name'][:20]:20} {c['rating']:>3}  est {c['price']:>6,}  +{c['score']:>6,}  {c['set'][:50]}")
    if not todo or (not dry and ask("\nApprove and start? Type YES: ") != "YES"):
        return
    budget, spent, n = session_budget(coins), 0, 0
    add, flush = relister(wa)
    wa.open_search()
    try:
        for c in todo:
            if n >= CFG["max_buys_per_session"]:
                break
            if spent + cap_for(c) > budget:
                continue
            price = buy_one(wa, c, state, dry)
            if price:
                spent, n = spent + price, n + 1
                if not dry:
                    add(c["resourceId"], price)
    except StopBot as e:
        print(f"\nSTOPPED: {e}")
    finally:
        if not dry:
            flush()
    print(f"\nDone: {n} cards, {spent:,} coins. In game: Optimize -> Regrade the sets they belong to.")


def cmd_login(br):
    br.page(GALLERY_URL)
    br.page(WEBAPP_URL)
    br.ensure_extension()
    ask("\nLog in to easysbc.io and to the EA web app in this Chrome window (never while FC 27 is open).\n"
        "Press Enter here when done... ")


def cmd_relist(br):
    state = load_state()
    wa, _ = connect_webapp(br)
    prices = {int(k): v["price"] for k, v in state["bought"].items() if k.isdigit() and not v.get("dry")}
    for name, d, binp, ok in wa.relist(prices, CFG["listing_duration_s"]):
        print(f"listed {name} at {binp:,}: {'ok' if ok else 'FAILED'}")


def cmd_test_buy(br, a):
    """Buy ONE named card and relist it at the price paid - an end-to-end smoke test of the web app part."""
    card = {"name": a.name, "fullName": a.full or a.name, "rating": a.rating, "price": a.price,
            "resourceId": f"test-{a.name}-{a.rating}", "set": "TEST"}
    cap = cap_for(card)
    wa = WebApp(br.page(WEBAPP_URL), CFG, log_row)
    coins = wa.ensure_ready(interactive=False)
    print(f"Web app ready. Coins: {coins:,}. Test card {a.name} {a.rating}, cap {cap:,}", flush=True)
    wa.open_search()
    price, note = wa.buy_card(card, cap)
    log_row([dt.datetime.now().strftime("%H:%M:%S"), "TEST", a.name, a.rating, "", a.price, cap, price or "", note])
    print(f"BUY RESULT: {('BOUGHT ' + str(price)) if price else 'NOT BOUGHT'} - {note}", flush=True)
    if not price:
        return
    js = r"""async () => { const obs = window.__obs; const tl = await obs(services.Item.requestTransferItems());
      const items = (tl.data && tl.data.items) || (tl.response && tl.response.items) || [];
      return items.filter(i => !(i._auction && ['active','closed'].includes(i._auction.tradeState)))
                  .map(i => [i.definitionId, i._staticData && i._staticData.name, i.rating]); }"""
    target = [d for d, n, r in wa.p.evaluate(js) if r == a.rating and a.name.lower() in (n or "").lower()]
    if not target:
        print("Could not find the bought card on the transfer list to relist.", flush=True)
        return
    for name, d, binp, ok in wa.relist({target[0]: price}, CFG["listing_duration_s"]):
        print(f"RELIST RESULT: {name} at {binp:,}: {'ok' if ok else 'FAILED'}", flush=True)
    print(f"Coins now: {wa.coins():,}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="full",
                    choices=["full", "plan", "login", "relist", "test-buy", "levels"])
    ap.add_argument("--dry-run", action="store_true", help="full/levels: check live prices only, buy nothing")
    ap.add_argument("--name", help="test-buy: card name as typed in the TM search")
    ap.add_argument("--full", help="test-buy: full name to disambiguate suggestions")
    ap.add_argument("--rating", type=int, help="test-buy: card rating")
    ap.add_argument("--price", type=int, help="test-buy: expected price (cap is derived from it)")
    ap.add_argument("--no-wait", action="store_true", help="close the window at the end without waiting for Enter")
    a = ap.parse_args()
    with Browser() as br:
        try:
            {"full": lambda: cmd_full(br, a.dry_run), "plan": lambda: cmd_full(br, True, plan_only=True),
             "login": lambda: cmd_login(br), "relist": lambda: cmd_relist(br),
             "test-buy": lambda: cmd_test_buy(br, a), "levels": lambda: cmd_levels(br, a.dry_run)}[a.command]()
        except StopBot as e:
            print(f"STOPPED: {e}", flush=True)
        except KeyboardInterrupt:
            print("\nCancelled.")
        if a.command != "login" and not a.no_wait:
            ask("\nPress Enter to close the bot's Chrome window... ")


if __name__ == "__main__":
    main()
