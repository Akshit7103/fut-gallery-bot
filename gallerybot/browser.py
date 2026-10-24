"""One dedicated Chrome window (its own profile in ./chrome-profile). You log in to EasySBC and the EA web app
in it yourself once; the bot never sees or types any password."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "chrome-profile"
# EasySBC's "Club Import" extension. The bot window has its own profile, so it needs the extension added once there
# (Chrome no longer lets programs side-load extensions; it has to come from the Web Store).
EXT_ID = "mchecdiinfipdfihkoebfbpfnllbllhc"
EXT_STORE_URL = f"https://chromewebstore.google.com/detail/easysbc-club-import/{EXT_ID}"


class Browser:
    def __enter__(self):
        PROFILE.mkdir(exist_ok=True)
        self.pw = sync_playwright().start()
        self.ctx = self.pw.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False, viewport=None,
            ignore_default_args=["--disable-extensions"],      # so the EasySBC Club Import extension can live here
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"])
        self.ctx.set_default_timeout(20000)
        return self

    def page(self, url: str, newest=False):
        """Reuse a tab already on this site, else open a new one. newest=True keeps only the most recently opened
        tab on that site (the EasySBC import opens its own web app tab; an older one would be logged out)."""
        host = url.split("/")[2]
        same = [p for p in self.ctx.pages if host in p.url]
        if newest and len(same) > 1:
            for p in same[:-1]:
                p.close()
            same = same[-1:]
        for p in same:
            p.bring_to_front()
            return p
        p = self.ctx.pages[0] if len(self.ctx.pages) == 1 and self.ctx.pages[0].url in ("about:blank", "") else self.ctx.new_page()
        p.goto(url, wait_until="domcontentloaded")
        p.bring_to_front()
        return p

    @staticmethod
    def has_extension():
        d = PROFILE / "Default" / "Extensions" / EXT_ID
        return d.is_dir() and any(d.iterdir())

    def ensure_extension(self, wait_minutes=10):
        """Make sure EasySBC's Club Import extension is in this window; if not, open its store page for you to add."""
        if self.has_extension():
            return True
        p = self.ctx.new_page()
        p.goto(EXT_STORE_URL, wait_until="domcontentloaded")
        p.bring_to_front()
        print("\nThe bot window needs EasySBC's 'Club Import' extension (one time only - your normal Chrome's copy is "
              "not shared with it).\nIn the bot window: click 'Add to Chrome' -> 'Add extension'. Waiting...", flush=True)
        end = time.time() + wait_minutes * 60
        while time.time() < end:
            if self.has_extension():
                time.sleep(2)
                print("Extension added.", flush=True)
                try:
                    p.close()
                except Exception:
                    pass
                return True
            time.sleep(2)
        print("Extension not added - skipping the club import.", flush=True)
        return False

    def __exit__(self, *exc):
        try:
            self.ctx.close()
        finally:
            self.pw.stop()
