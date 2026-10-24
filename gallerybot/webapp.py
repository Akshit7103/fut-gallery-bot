"""EA FC 27 web app actions: coins, transfer-market search, Buy Now, send to transfer list, relist.

Buying uses real (trusted) clicks through Playwright; EA ignores synthetic JS clicks on 'Buy Now'.
Listing uses the web app's own JS services (services.Item.list), which works reliably.
The bot never types credentials: if the web app shows its Login screen it stops and waits for you."""
import random
import re
import time
import unicodedata
from pathlib import Path

WEBAPP_URL = "https://www.ea.com/ea-sports-fc/ultimate-team/web-app/"

JS_HELPERS = r"""() => {
  window.__obs = window.__obs || (o => new Promise(res => o.observe(window, (s, r) => res(r))));
  return true;
}"""
JS_COINS = r"""() => { try { return services.User.getUser().getCurrency(GameCurrency.COINS).amount; } catch (e) { return null; } }"""
THROTTLE_PAT = re.compile(r"(too many|temporarily|try again later|capped|banned|461|verification)", re.I)


class StopBot(Exception):
    pass


def _norm(name):
    """Lower-case, accents removed - for comparing player names."""
    return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().strip()


def name_match(shown, full, short):
    """How well a name in EA's suggestion list fits the wanted player (0..1). The rating is checked separately.
    A different first name (Kyle Smith vs Jordan Smith) never passes."""
    sh, fu, so = _norm(shown), _norm(full), _norm(short)
    if not sh:
        return 0.0
    if sh in (fu, so):
        return 1.0
    t_sh, t_fu = sh.split(), (fu or so).split()
    if fu and (sh in fu or fu in sh) and (len(t_sh) == 1 or t_sh[0][0] == t_fu[0][0]):
        return 0.9                               # 'Odegaard' in 'Martin Odegaard'
    if set(t_sh) <= set(t_fu) and t_sh[0][0] == t_fu[0][0]:
        return 0.85                              # 'Brandon Vazquez' within 'Brandon Vazquez Toledo'
    if len(t_sh) > 1 and len(t_fu) > 1 and t_sh[-1] == t_fu[-1] and t_sh[0][0] == t_fu[0][0]:
        return 0.75                              # same surname, same first initial
    return 0.0


class WebApp:
    def __init__(self, page, cfg, log):
        self.p, self.cfg, self.log = page, cfg, log

    # ---------- helpers ----------
    def pause(self, key="delay_between_actions_s"):
        lo, hi = self.cfg[key]
        time.sleep(random.uniform(lo, hi))

    def text(self):
        try:
            return self.p.inner_text("body")
        except Exception:
            return ""

    def check_throttle(self):
        """Only toasts/dialogs are checked, so normal page text can't trigger a false stop."""
        try:
            msgs = self.p.locator(".ut-notification-view, .ea-dialog-view, .view-modal-container, "
                                  ".ui-dialog-type-message").all_inner_texts()
        except Exception:
            msgs = []
        m = THROTTLE_PAT.search(" ".join(msgs))
        if m:
            raise StopBot(f"EA warning on screen ('{m.group(0)}'). Stopping to protect your account.")

    def coins(self):
        """None while the web app is loading/redirecting or logged out."""
        try:
            return self.p.evaluate(JS_COINS)
        except Exception:
            return None

    # ---------- session ----------
    def ensure_ready(self, interactive=True, wait_minutes=10):
        if "ultimate-team/web-app" not in self.p.url:
            self.p.goto(WEBAPP_URL, wait_until="domcontentloaded")
        for _ in range(45):
            c = self.coins()
            if c:
                self.p.evaluate(JS_HELPERS)
                return c
            time.sleep(1)
        if not interactive:
            print(f"Waiting up to {wait_minutes} min for you to log in to the web app in the bot's Chrome window...", flush=True)
            end = time.time() + wait_minutes * 60
            while time.time() < end:
                c = self.coins()
                if c:
                    self.p.evaluate(JS_HELPERS)
                    return c
                time.sleep(3)
            raise StopBot("Timed out waiting for the web app login.")
        input("\nThe web app is not logged in. Log in yourself in the Chrome window (only when FC 27 is NOT open "
              "on your PC - one session at a time), wait for the Home screen, then press Enter here... ")
        c = self.coins()
        if not c:
            raise StopBot("Still not logged in to the web app.")
        self.p.evaluate(JS_HELPERS)
        return c

    def open_search(self):
        self.p.get_by_text("Transfers", exact=True).first.click()
        self.pause()
        self.p.get_by_text("Search the Transfer Market", exact=True).first.click()
        self.p.wait_for_selector("input.ut-text-input-control", timeout=15000)
        self.pause()

    def back(self):
        try:
            self.p.locator(".ut-navigation-button-control").first.click()
        except Exception:
            pass
        self.pause()

    # ---------- typing ----------
    def type_checked(self, loc, text, digits=False):
        """Type into a web app box and read it back; retype if a key was lost (the web app can swallow the first
        key while it focuses the box). Returns True when the box holds exactly `text`."""
        want = re.sub(r"\D", "", text) if digits else text
        for attempt in range(3):
            loc.click()
            time.sleep(0.3 + 0.3 * attempt)      # let the box take focus before the first key
            loc.press("Control+a")
            loc.press("Backspace")
            time.sleep(0.2)
            loc.type(text, delay=90 + 60 * attempt)
            time.sleep(0.2)
            got = loc.input_value()
            if (re.sub(r"\D", "", got) if digits else got) == want:
                return True
            print(f"      (typed '{text}' but the box shows '{got}' - retyping)", flush=True)
        return False

    # ---------- search ----------
    def _suggestions(self, text):
        """Type `text` in the player-name box and return the suggestion list as [(name, rating)]."""
        box = self.p.locator("input.ut-text-input-control").first
        sugs = self.p.locator(".playerResultsList button")
        for attempt in range(2):                  # suggestions can take a few seconds to appear
            if not self.type_checked(box, text):
                continue
            for _ in range(16):
                time.sleep(0.5)
                texts = [t.strip() for t in sugs.all_inner_texts()]
                if texts:
                    out = []
                    for t in texts:
                        m = re.match(r"(.*?)\s*(\d+)$", t, re.S)
                        out.append((m.group(1).strip(), m.group(2)) if m else (t, ""))
                    return out
        return []

    def _clear_name(self):
        try:
            box = self.p.locator("input.ut-text-input-control").first
            box.click()
            box.press("Control+a")
            box.press("Backspace")
        except Exception:
            pass

    def search(self, card, max_bin):
        """Pick the player from EA's own name suggestions: the right rating, then the closest name (short card name
        first, the full name when the short one has too many matches). Set Buy Now max, Search.
        Returns (rows, None) on the results page, or (None, error) still on the search page."""
        full = card.get("fullName") or ""
        queries = [card["name"]] + ([full] if full and _norm(full) != _norm(card["name"]) else [])
        rating, pick, seen = str(card["rating"]), None, []
        for q in queries:
            sug = self._suggestions(q)
            seen = sug
            cand = [i for i, (_, r) in enumerate(sug) if r == rating]
            if not cand:
                continue
            score = {i: name_match(sug[i][0], full, card["name"]) for i in cand}
            ranked = sorted(cand, key=lambda i: score[i], reverse=True)
            top = ranked[0]
            if score[top] >= 0.75 and (len(ranked) == 1 or score[top] > score[ranked[1]]):
                pick = top
                self.last_pick = f"{sug[top][0]} {sug[top][1]}"
                break
        if pick is None:
            try:
                self.p.screenshot(path=str(Path(__file__).resolve().parent.parent / "logs" / "debug_search.png"))
            except Exception:
                pass
            self._clear_name()
            shown = ", ".join(f"{n} {r}" for n, r in seen[:5]) or "no suggestions"
            return None, f"not found in EA's name suggestions ({shown})"
        self.p.locator(".playerResultsList button").nth(pick).click()
        self.pause()
        num = self.p.locator("input.ut-number-input-control").last
        if not self.type_checked(num, str(max_bin), digits=True):
            self._clear_name()
            return None, f"could not set Buy Now max to {max_bin}"
        self.pause()
        self.p.get_by_role("button", name=re.compile(r"^Search$")).click()
        time.sleep(3.0)
        self.check_throttle()
        rows = []
        for i, t in enumerate(self.p.locator(".listFUTItem").all_inner_texts()):
            m = re.search(r"Buy Now:\s*([\d,]+)", t)
            if m:
                rows.append((i, int(m.group(1).replace(",", ""))))
        return rows, None

    def buy_from_results(self, rows, max_bin):
        """Try the cheapest rows (most recently listed first). Returns price paid or None if every try was sniped."""
        if not rows:
            return None
        best = min(b for _, b in rows)
        if best > max_bin:
            return None
        cands = [i for i, b in rows if b == best][::-1]
        for i in cands[:2]:
            self.p.locator(".listFUTItem").nth(i).click()
            self.pause()
            btn = self.p.get_by_role("button", name=re.compile(r"^Buy Now for"))
            label = btn.inner_text()
            price = int(re.sub(r"\D", "", label) or 0)
            if not price or price > max_bin:
                continue
            btn.click()
            self.p.get_by_role("button", name=re.compile(r"^Ok$")).click(timeout=8000)
            time.sleep(2.5)
            self.check_throttle()
            if "Congratulations" in self.text():
                self.p.get_by_text("Send to Transfer List", exact=True).first.click()
                self.pause()
                return price
        return None

    def buy_card(self, card, max_bin):
        """Search + buy with retries (fresh search each retry). Returns (price or None, note)."""
        for attempt in range(self.cfg["retries_per_card"]):
            rows, err = self.search(card, max_bin)
            if err:                               # still on the search page - nothing to go back from
                return None, err
            price = self.buy_from_results(rows, max_bin)
            self.back()
            if price:
                return price, f"bought on try {attempt + 1}"
            if not rows:
                return None, f"no listings at or under {max_bin}"
            self.pause("delay_between_buys_s")
        return None, "sniped every try"

    # ---------- relist ----------
    def relist(self, prices_by_def, duration):
        """List every unlisted transfer-list item whose definitionId we bought, at the price we paid (floor)."""
        js = r"""async ([prices, duration]) => {
          const obs = window.__obs;
          const step = p => p > 10000 ? 250 : p > 1000 ? 100 : 50;
          const tl = await obs(services.Item.requestTransferItems());
          const items = (tl.data && tl.data.items) || (tl.response && tl.response.items) || [];
          const out = [];
          for (const it of items) {
            const bin = prices[String(it.definitionId)];
            const st = it._auction && it._auction.tradeState;
            if (!bin || st === 'active' || st === 'closed') continue;
            await obs(services.Item.requestMarketData(it));
            const r = await obs(services.Item.list(it, Math.max(150, bin - step(bin)), bin, duration));
            out.push([it._staticData && it._staticData.name, it.definitionId, bin, !!(r && r.success)]);
            await new Promise(x => setTimeout(x, 700));
          }
          return out;
        }"""
        return self.p.evaluate(js, [{str(k): v for k, v in prices_by_def.items()}, duration])
