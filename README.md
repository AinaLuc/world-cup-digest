# World Cup Digest Bot 🏆

A simple cron job that emails you the latest World Cup match results + YouTube
highlight links, every 3 hours during the tournament.

Built with ❤️ by **Mavis**.

## How it works

1. **Fetches match results** from the official
   [2026 FIFA World Cup Wikipedia page](https://en.wikipedia.org/wiki/2026_FIFA_World_Cup)
   (no API key needed).
2. **Searches YouTube** for the official highlight video of each newly-completed
   match using [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (also no API key).
3. **Sends a digest email** to your Gmail via SMTP using an App Password.
4. **Tracks state** in `state.json` (committed back to the repo) so each match
   is only emailed once.

Runs on **GitHub Actions — totally free**, no card needed.

## One-time setup

### 1. Create a GitHub repo
- Sign in / sign up at [github.com](https://github.com).
- Click **New repository**, name it `world-cup-digest` (or anything you like).
- Choose **Public** (free, unlimited GitHub Actions minutes).
- **Don't** initialize with README / .gitignore.

### 2. Push the code
In a terminal:
```bash
cd /path/to/this/folder
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/world-cup-digest.git
git push -u origin main
```

### 3. Add 3 secrets to the repo
Go to your repo on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add these three:

| Name | Value |
| --- | --- |
| `GMAIL_USER` | Your Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | The 16-char App Password from https://myaccount.google.com/apppasswords (requires 2FA on) |
| `RECIPIENT` | Email address to receive the digest (defaults to `GMAIL_USER` if not set) |

### 4. Enable GitHub Actions
- Go to the **Actions** tab in your repo, click **"I understand my workflows, go ahead and enable them"** if prompted.

### 5. (Optional) Test it
- Actions tab → **World Cup Digest** → **Run workflow** → green button.
- Check your email in ~30 seconds.

## Customization

- **Schedule**: edit the `cron` line in `.github/workflows/cron.yml`.
  Examples:
  - `0 */1 * * *` — every hour
  - `0 8,20 * * *` — 8 AM and 8 PM UTC only
  - `0 0 * * *` — once a day, midnight UTC
- **Tournament year**: change `WORLD_CUP_YEAR` in `world_cup_bot.py`.
- **Email styling**: edit `render_email()` in `world_cup_bot.py`.

## Files

```
world_cup_bot.py          # the main script
requirements.txt          # Python deps
.github/workflows/cron.yml  # GitHub Actions schedule
state.json                # auto-generated; tracks emailed matches
```

## License

MIT — do whatever you want with it.
