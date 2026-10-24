# FUT Gallery Tokens Bot

Gets **Gallery Tokens** in EA FC 27 Ultimate Team for the fewest coins:

EasySBC's Gallery token plan → buy the missing cards on the EA web app → list them straight back at the price paid → you complete the sets in game.

It works for any account: it uses whatever EA and EasySBC logins you make in its own Chrome window. It never sees or types a password.

## Risk (read this)
EA's rules ban automated scripts on the web app. Buying with a script can get you a **transfer-market ban**. The bot lowers the risk but can't remove it:
- you approve the plan (type `YES`) before anything is bought
- random delays between actions and buys
- caps per card and per run (coins and number of buys), plus a coin reserve
- it stops immediately if EA shows a "too many actions" / verification message

Never run it while FC 27 or the companion app is open. EA allows one session at a time.

## Needs
- Windows, **Google Chrome**, **Python 3.10+** (tick "Add python.exe to PATH" when installing)
- A free **easysbc.io** account. Each run can spend 1 of its daily AI credits to re-solve the Gallery sets.

## Run it
Double-click **`GalleryBot.bat`**. The first run installs the one library it needs (Playwright).

A separate Chrome window opens with two tabs: EasySBC's Gallery page and the EA web app. Then:

1. **Log in** to both tabs yourself (first time only; the window remembers you).
2. The bot reads your **coin balance**.
3. **Import club**: the bot opens EasySBC's import page. The first time, click *Club Import Extension* and add it to this Chrome window. Then click **START IMPORT** and press Enter in the bot window when EasySBC says it's done.
4. **Re-solve** (optional, 1 EasySBC AI credit): EasySBC rebuilds every set's solution for your club as it is now.
5. **Token plan**: EasySBC's Gallery Planner picks the sets and grades that give the most tokens for your coins. The bot then opens every set and reads its cards: owned, to buy, prices. It shows:
   - **FREE**: you already own every card. Just complete these in game.
   - **BLOCKED**: needs a card the bot can't buy (objective reward, token-store card, no price).
   - **TO BUY**: sets ranked by tokens per coin. `*` marks the sets in this run: the most tokens that fit your coins and the per-run card limit. Only whole sets count; half a set gives no tokens.
   - **Expected tokens** after this run, and after the whole plan.
6. If a listed set is **already at that grade in game**, type its number and grade (e.g. `4=B 9=S`). The bot marks it on EasySBC and re-plans.
7. Type **`YES`**. The bot buys each card at or under its cap, sends it to the transfer list, and lists it at the price paid (1 hour).
8. **In game:** open each set it lists, press **Optimize** (or Autocomplete) and submit at the grade shown. Cards still count as collected after you sell them.
9. Run it again after the cards sell. It asks whether you completed last run's sets, marks them on EasySBC, and continues with the next ones.

`GalleryBot-DryRun.bat` does everything except buying: it searches the live market and shows what each card costs right now.

### Other commands
`python run.py plan` builds and shows the plan only · `python run.py relist` lists bot-bought cards still sitting unlisted · `python run.py levels` is the older flow aimed at Gallery **level** score instead of tokens · `python run.py --help`

## Settings (`config.json`)
| key | meaning |
|---|---|
| `max_buys_per_session`, `max_coins_per_session` | hard limits per run |
| `coin_reserve` | never go below this balance |
| `price_cap_margin_pct`, `price_cap_min_extra` | cap = EasySBC price + max(margin %, min extra) |
| `absolute_max_per_card` | never pay more than this for one card |
| `batch_size` | list bought cards every N buys |
| `delay_between_actions_s`, `delay_between_buys_s` | random human-like waits |
| `listing_duration_s` | listing duration (3600 = 1 hour) |
| `retries_per_card` | fresh searches when a card is sniped |

## Files (personal; never share them)
- `chrome-profile/`: the bot window's logins
- `logs/buys_YYYYMMDD.csv`: every buy attempt (set, card, cap, price paid, note)
- `logs/state.json`: cards already bought (never bought twice) and sets waiting to be completed in game
- `logs/last_token_plan.json`: the last plan, with every set's cards

## Known limits
- EasySBC only knows the grades you have marked, so fix them at step 6. The EA web app has no Gallery data the bot could read instead.
- Completing a set in game is still manual. The web app can't do it.
- Cards are chosen from EA's own name suggestions: right rating plus a matching name (the full name is used when the short name has too many matches). If it can't find the player, it skips the card and logs why.
- The web app must be in **English**. If EA changes the web app layout, the selectors in `gallerybot/webapp.py` need updating.
