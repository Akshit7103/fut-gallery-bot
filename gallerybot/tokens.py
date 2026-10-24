"""Gallery TOKENS plan from easysbc.io/fut-gallery (the main Gallery page).

The page's Gallery Planner takes a coin balance and picks the sets + grades that give the most Gallery Tokens for it
(free - it uses the set solutions EasySBC already built for your club). Each set's solution page
/fut-gallery/<category>/<set>?grade=<G> lists the cards for that grade: collected ones and the ones to buy.
EasySBC's own method: buy the missing cards, sell them straight away at the same price (EA still marks them
collected), then complete the set in game."""
import re
import time

GALLERY_URL = "https://www.easysbc.io/fut-gallery"
GRADES = ["D", "C", "B", "A", "S"]

_FIND = r"""const __find = (want) => { const res = []; const seen = new Set();
  for (const e of document.querySelectorAll('div,section,main,article,form,ul')) {
    const fk = Object.keys(e).find(k => k.startsWith('__reactFiber')); if (!fk) continue;
    let f = e[fk];
    for (let j = 0; j < 4 && f; j++, f = f.return) {
      const p = f.memoizedProps;
      if (p && typeof p === 'object' && want.every(k => k in p) && !seen.has(p)) { seen.add(p); res.push(p); }
    } }
  return res; };"""

JS_PLANNER = "() => {" + _FIND + r"""
  const r = __find(['onBuild', 'tokensPlaceholder']); if (!r.length) return null;
  const x = r[0]; return {open: x.open, building: x.building, error: x.error ? String(x.error) : null, plan: x.plan || null};
}"""

JS_SETS = "() => {" + _FIND + r"""
  return __find(['cardGrades', 'set', 'progress']).map(c => ({
    id: c.set.id, name: c.set.name, slug: c.set.slug, category: c.categorySlug, size: c.set.requiredCards,
    completed: c.progress && c.progress.completedGrade,
    rewards: (c.set.grades || []).map(g => [g.name, Object.values(g.tokenRewards || {}).reduce((a, b) => a + b, 0)]),
    grades: (c.cardGrades || []).map(g => ({grade: g.grade, budget: g.budgetNeeded, tax: g.taxLoss,
                                            collected: g.collectedCount, size: g.size}))}));
}"""

JS_SOLUTION = "() => {" + _FIND + r"""
  const r = __find(['items', 'onToggleCollected']); if (!r.length) return null;
  const g = __find(['grades', 'selectedIndex', 'completedIndex']);
  return {grade: g.length ? (g[0].grades[g[0].selectedIndex] || {}).name : null,
          items: r[0].items.map(x => ({resourceId: x.resourceId, collected: !!x.collected, firstOwner: !!x.firstOwner,
            price: x.price || (x.player && x.player.priceInfo && x.player.priceInfo.displayPrice) || 0,
            source: x.player && x.player.priceInfo && x.player.priceInfo.source, score: x.score || 0,
            name: x.player && x.player.cardName, fullName: x.player && x.player.name,
            rating: x.player && x.player.rating, position: x.player && x.player.preferredPosition}))};
}"""


JS_BUILD = "() => {" + _FIND + r"""
  const r = __find(['build', 'sync', 'clubImported']); if (!r.length) return null;
  const b = r[0].build || {};
  return {imported: !!r[0].clubImported, lastImportedAt: r[0].lastImportedAt || null, running: !!b.running,
          built: !!b.built, disabled: !!b.disabled, done: b.progress ? b.progress.done : null,
          total: b.progress ? b.progress.total : null, newCollected: b.newCollectedCount || 0};
}"""


def _wait(page, js, ok, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            d = page.evaluate(js)
        except Exception:                        # page still navigating
            d = None
        if d is not None and ok(d):
            return d
        time.sleep(1)
    return None


def account(page):
    """Load the Gallery page and report: logged in, club imported (when), AI credits left, Gallery token balance."""
    caught = {}

    def on_resp(r):
        u = r.url.split("?")[0]
        if "api-fc27.easysbc.io" in u and (u.endswith("/credits") or u.endswith("/gallery/progress")):
            try:
                caught[u.rsplit("/", 1)[-1]] = r.json()
            except Exception:
                pass
    page.on("response", on_resp)
    try:
        page.goto(GALLERY_URL, wait_until="domcontentloaded")
        b = _wait(page, JS_BUILD, lambda d: True, 40)
        end = time.time() + 10
        while time.time() < end and len(caught) < 2:
            time.sleep(0.5)
        txt = page.inner_text("body")
    finally:
        page.remove_listener("response", on_resp)
    daily = ((caught.get("credits") or {}).get("buckets") or {}).get("daily") or {}
    m_imp = re.search(r"Last club import:\s*([^\n]+)", txt)
    imported = bool(b and b["imported"]) or "Your club is imported" in txt
    last = m_imp.group(1).strip() if m_imp else None
    if b and b.get("lastImportedAt"):
        mins = int((time.time() - b["lastImportedAt"] / 1000) // 60)
        last = "just now" if mins < 1 else f"{mins} min ago" if mins < 120 else f"{mins // 60} hours ago"
    return {"logged_in": bool(caught.get("credits")) or imported,
            "imported": imported,
            "last_import": last,
            "credits": daily.get("remaining"),
            "credits_reset": daily.get("resetsAt"),
            "tokens": (caught.get("progress") or {}).get("tokenBalance"),
            "solutions_built": bool(b and b["built"]),
            "new_collected": b["newCollected"] if b else 0}


def import_club(page, timeout=900):
    """Press EasySBC's 'Import Club' (needs the Club Import extension in this window). The extension opens the EA
    web app and reads your club; returns True once EasySBC shows a newer import time."""
    if "/fut-gallery" not in page.url or "/fut-gallery/" in page.url:
        page.goto(GALLERY_URL, wait_until="domcontentloaded")
    b0 = _wait(page, JS_BUILD, lambda d: True, 40) or {}
    btn = page.get_by_role("button", name="Import Club", exact=True).first
    for _ in range(15):
        if btn.count() and btn.is_visible():
            break
        if page.get_by_text("Club Imported", exact=True).count():
            print("   EasySBC says the club was just imported - nothing to do.", flush=True)
            return True                          # for a while after an import EasySBC shows a label, not the button
        time.sleep(1)
    else:
        print("   EasySBC's Import Club button was not found.", flush=True)
        return False
    btn.click()
    end, t0 = time.time() + timeout, time.time()
    while time.time() < end:
        time.sleep(10)
        try:
            b = page.evaluate(JS_BUILD) or {}
        except Exception:
            b = {}
        if b.get("lastImportedAt") and b["lastImportedAt"] != b0.get("lastImportedAt"):
            return True
        if int(time.time() - t0) % 60 < 10:
            print(f"   importing... {int(time.time() - t0)}s (log in on the EA web app tab if it asks)", flush=True)
    return False


def update_solutions(page, timeout=600):
    """Press 'Update Solutions' (EasySBC re-solves every set for your club; costs 1 AI credit). True when done."""
    if "/fut-gallery" not in page.url or "/fut-gallery/" in page.url:
        page.goto(GALLERY_URL, wait_until="domcontentloaded")
    b = _wait(page, JS_BUILD, lambda d: True, 40)
    if not b or b["disabled"]:
        return False
    page.get_by_role("button", name=re.compile(r"^(Update|Build) Solutions$")).first.click()
    if not _wait(page, JS_BUILD, lambda d: d["running"], 20):
        return False                             # no credit left (EasySBC shows an upgrade box instead)
    end, last = time.time() + timeout, None
    while time.time() < end:
        time.sleep(3)
        b = page.evaluate(JS_BUILD) or {}
        if b.get("done") != last:
            last = b.get("done")
            print(f"   solving sets {b.get('done')}/{b.get('total')}", flush=True)
        if not b.get("running"):
            return True
    return False


def build_token_plan(page, budget):
    """Ask EasySBC's Gallery Planner for the most tokens for `budget` coins. Free (no daily solve)."""
    page.goto(GALLERY_URL, wait_until="domcontentloaded")
    st = _wait(page, JS_PLANNER, lambda d: True, 40)
    if st is None:
        raise RuntimeError("EasySBC's Gallery Planner did not load (logged in? club imported?).")
    if not st["open"]:
        page.get_by_role("button", name=re.compile(r"^Gallery Planner")).first.click()
        time.sleep(1.5)
    for label in ("Token target", "Max tax loss", "Max transfers", "Max sets"):   # only the coin limit applies
        box = page.locator(f"input[aria-label='{label}']").first
        if box.count() and box.input_value():
            box.fill("")
    coin = page.locator("input[aria-label='Coin balance']").first
    coin.fill(str(int(budget)))
    coin.press("Enter")
    d = _wait(page, JS_PLANNER, lambda d: not d["building"] and d["plan"]
              and int((d["plan"].get("input") or {}).get("budget") or -1) == int(budget), 90)
    if not d:
        raise RuntimeError("EasySBC did not return a token plan for that coin balance.")
    return d["plan"]


def read_sets(page):
    """All Gallery sets shown on the main page (id -> name, slug, category, completed grade, token rewards)."""
    if "/fut-gallery" not in page.url or "/fut-gallery/" in page.url:
        page.goto(GALLERY_URL, wait_until="domcontentloaded")
    sets = _wait(page, JS_SETS, lambda d: len(d) > 0, 40) or []
    return {s["id"]: s for s in sets}


def read_solution(page, s, grade):
    """The cards EasySBC picked for set `s` at `grade` (collected + to buy)."""
    url = f"{GALLERY_URL}/{s['category']}/{s['slug']}?grade={grade}"
    page.goto(url, wait_until="domcontentloaded")
    d = _wait(page, JS_SOLUTION, lambda d: d["items"] and d["grade"] in (grade, None), 30)
    return d["items"] if d else None


def tokens_between(s, done, target):
    """Tokens you get going from grade `done` (None = nothing yet) up to `target`."""
    lo = GRADES.index(done) + 1 if done in GRADES else 0
    hi = GRADES.index(target)
    rew = dict(s["rewards"])
    return sum(rew.get(g, 0) for g in GRADES[lo:hi + 1])


def mark_completed(page, marks):
    """Tell EasySBC which grade a set is already at in game ({setId: 'B', ...}) - same as the grade marker on a set's
    page. The planner then stops counting tokens you already have."""
    if "/fut-gallery" not in page.url or "/fut-gallery/" in page.url:
        page.goto(GALLERY_URL, wait_until="domcontentloaded")
    _wait(page, JS_SETS, lambda d: len(d) > 0, 40)
    n = page.evaluate("(marks) => {" + _FIND + r"""
      let n = 0;
      for (const c of __find(['cardGrades', 'set', 'progress', 'onCompletedGradeChange'])) {
        const g = marks[String(c.set.id)];
        if (g) { c.onCompletedGradeChange(c.set.id, g); n++; }
      }
      return n; }""", {str(k): v for k, v in marks.items()})
    time.sleep(3)                                # let the saves reach EasySBC
    return n


def read_token_plan(page, budget):
    """Build the plan for `budget`, then read each planned set's card list. Returns
    {'totals', 'sets': [{id, name, grade, done, tokens, buy:[cards], have}], 'missing': [set names]}."""
    plan = build_token_plan(page, budget)
    meta = read_sets(page)
    out, missing = [], []
    for ps in plan["sets"]:
        s = meta.get(ps["setId"])
        if not s:
            missing.append(str(ps["setId"]))
            continue
        items = read_solution(page, s, ps["grade"])
        if items is None:
            missing.append(s["name"])
            continue
        buy = [{**it, "set": s["name"], "setId": s["id"]} for it in items if not it["collected"]]
        out.append({"id": s["id"], "name": s["name"], "grade": ps["grade"], "done": s["completed"],
                    "tokens": tokens_between(s, s["completed"], ps["grade"]), "buy": buy,
                    "have": sum(1 for it in items if it["collected"])})
    return {"totals": plan.get("totals") or {}, "budget": budget, "sets": out, "missing": missing}
