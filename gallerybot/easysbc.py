"""Read the Gallery Level Planner plan from easysbc.io/fut-gallery/levels (your synced club must be imported there).

The page keeps the plan in React props: plan.sets = [{setId, name, grade, totalScore, toBuy, taxLoss, group, done}],
setItems[setId] = [{resourceId, source: 'collected'|'market', price, score, firstOwner, player{...}}] (loaded per set
when its row is opened)."""
import re
import time

LEVELS_URL = "https://www.easysbc.io/fut-gallery/levels"
IMPORT_URL = "https://www.easysbc.io/club-import-tutorial"

JS_READ = r"""() => {
  for (const e of document.querySelectorAll('div')) {
    const fk = Object.keys(e).find(k => k.startsWith('__reactFiber'));
    if (!fk) continue;
    let f = e[fk];
    for (let j = 0; j < 6 && f; j++, f = f.return) {
      const p = f.memoizedProps;
      if (p && p.plan && p.setItems) {
        const items = {};
        for (const [k, v] of Object.entries(p.setItems)) items[k] = (v || []).map(x => ({
          resourceId: x.resourceId, source: x.source, price: x.price || 0, score: x.score || 0,
          firstOwner: !!x.firstOwner, name: x.player && (x.player.cardName || x.player.name),
          fullName: x.player && x.player.name, rating: x.player && x.player.rating,
          clubId: x.player && x.player.clubId, position: x.player && x.player.preferredPosition}));
        return {level: p.plan.level, totals: p.plan.totals, coinBalance: p.plan.coinBalance,
                computedAt: p.plan.computedAt, sets: p.plan.sets, items};
      }
    }
  }
  return null;
}"""


JS_LOAD = r"""async (ids) => {
  let pp = null;
  for (const e of document.querySelectorAll('div')) {
    const fk = Object.keys(e).find(k => k.startsWith('__reactFiber'));
    if (!fk) continue;
    let f = e[fk];
    for (let j = 0; j < 6 && f; j++, f = f.return) { const p = f.memoizedProps; if (p && p.plan && p.loadSetItems) { pp = p; break; } }
    if (pp) break;
  }
  if (!pp) return 0;
  let n = 0;
  for (const id of ids) { try { const x = pp.loadSetItems(id); if (x && x.then) await x; n++; } catch (e) {} }
  return n;
}"""


def status(page):
    """What the levels page says about your account: logged in? club imported (and when)? solves left today?"""
    page.goto(LEVELS_URL, wait_until="domcontentloaded")
    has_plan = bool(_wait_plan(page, 25))        # the import box only fills in once the account data has loaded
    txt = ""
    for _ in range(10):
        txt = page.inner_text("body")
        if "Last club import" in txt or "Your club is imported" in txt or "Log in" in txt[:800]:
            break
        time.sleep(1)
    m_imp = re.search(r"Last club import:\s*([^\n]+)", txt)
    m_sol = re.search(r"(\d+)\s*left today", txt)
    imported = "Club Imported" in txt or "Your club is imported" in txt or bool(m_imp)
    return {"imported": imported,
            "last_import": m_imp.group(1).strip() if m_imp else None,
            "solves_left": int(m_sol.group(1)) if m_sol else None,
            "logged_in": has_plan or imported or
                         ("My Club" in txt[:800] and not re.search(r"\b(Log in|Sign in)\b", txt[:800]))}


def build_plan(page, level, coin_balance):
    """Pick the target level, set the coin balance and press Build/Update Level N Plan (uses 1 EasySBC daily solve).
    Returns True when a fresh plan for that level is on the page."""
    page.goto(LEVELS_URL, wait_until="domcontentloaded")
    before = (_wait_plan(page, 20) or {}).get("computedAt")
    try:
        if not page.get_by_text(f"Level {level}", exact=True).first.is_visible():
            page.get_by_role("button", name=re.compile("Level Planner")).first.click()
            time.sleep(1)
        page.get_by_text(f"Level {level}", exact=True).first.click()
        time.sleep(1)
        coin = page.locator("xpath=//*[normalize-space(text())='COIN BALANCE' or normalize-space(text())='Coin balance']"
                            "/following::input[1]").first
        coin.click()
        coin.press("Control+a")
        coin.type(str(int(coin_balance)), delay=30)
        time.sleep(0.5)
        btn = page.get_by_role("button", name=re.compile(rf"(Build|Update) Level {level} Plan")).first
        if btn.is_disabled():
            print("EasySBC's Build button is disabled (no free plan builds left?).")
            return False
        btn.click(timeout=8000)
    except Exception as e:
        print(f"Could not start the EasySBC plan build: {e.__class__.__name__}")
        return False
    end = time.time() + 180                       # a new plan has a new computedAt timestamp
    while time.time() < end:
        time.sleep(3)
        data = page.evaluate(JS_READ)
        if data and str(data.get("level")) == str(level) and data.get("computedAt") != before:
            return True
    return False


def _wait_plan(page, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        data = page.evaluate(JS_READ)
        if data and data.get("sets"):
            return data
        time.sleep(1)
    return None


def read_plan(page):
    """Returns {'level','totals','sets','buy':[cards],'regrade':[set names]} for the plan currently built on EasySBC."""
    data = None
    for _ in range(2):                           # the page sometimes needs a reload before the plan renders
        page.goto(LEVELS_URL, wait_until="domcontentloaded")
        data = _wait_plan(page, 45)
        if data:
            break
    if not data:
        raise RuntimeError("No plan found on EasySBC. Log in, import/sync your club, pick a target level in the "
                           "Level Planner and press 'Build/Update Level Plan', then run again.")
    buy_sets = [s for s in data["sets"] if s.get("toBuy", 0) > 0 and not s.get("done")]
    for attempt in range(4):                     # EasySBC's own loader fetches each set's card list
        missing = [s["setId"] for s in buy_sets if not data["items"].get(str(s["setId"]))]
        if not missing:
            break
        page.evaluate(JS_LOAD, missing)
        time.sleep(3 + 3 * attempt)
        data = _wait_plan(page) or data
    buy, seen = [], {}
    for s in buy_sets:
        for it in data["items"].get(str(s["setId"]), []):
            if it["source"] == "collected":
                continue
            rid = it["resourceId"]
            if rid in seen:                      # one card can count in several sets (rarity + club + league)
                seen[rid]["set"] += " + " + s["name"]
                seen[rid]["score"] += it["score"]
                continue
            seen[rid] = {**it, "set": s["name"], "setId": s["setId"]}
            buy.append(seen[rid])
    regrade = [s["name"] for s in data["sets"] if not s.get("done")]
    missing = [s["name"] for s in buy_sets if not data["items"].get(str(s["setId"]))]
    return {"level": data["level"], "totals": data["totals"], "sets": data["sets"],
            "buy": buy, "regrade": regrade, "unloaded_sets": missing}
