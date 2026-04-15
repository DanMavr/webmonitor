# WebMonitor

Screenshot-based web change monitor. Watches pages using Playwright + EasyOCR.
Sends Telegram alerts with the changed text and a screenshot attachment.

## How it works

1. **Playwright** takes a headless screenshot of each page, clipped to a
   region you define visually in the dashboard
2. **EasyOCR** extracts text from the screenshot
3. **diff** compares it to the stored baseline text
4. If changed → **Telegram** alert with added/removed lines + screenshot

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium   # may need sudo on Linux

cp .env.example .env
# edit .env — add your Telegram token and chat ID

python run.py start
# Dashboard at http://localhost:5000
```

## Raspberry Pi (ARM64)

Playwright supports ARM64 natively since v1.37 and downloads an ARM64
Chromium build automatically.

If the bundled Chromium fails (older Pi OS, low RAM):
```bash
sudo apt install chromium-browser
```
Then add to `.env`:
```
USE_SYSTEM_CHROMIUM=1
```

## Adding a site

1. Open the dashboard → **Add Site**
2. Enter the site name and URL
3. Click **Fetch page screenshot** — the page loads in the form
4. **Drag a rectangle** over the area you want monitored
5. Set schedule interval, OCR languages, JS wait time
6. **Save Site** — monitoring starts immediately

## OCR languages

| Language | Code |
|----------|------|
| English  | `en` |
| Mongolian Cyrillic | `ru` |
| Russian  | `ru` |

Set `ocr_languages: [en, ru]` for sites with mixed English/Mongolian text.

## File structure

```
webmonitor/
  run.py                    ← entry point
  config/
    sites.yaml              ← site configuration
  monitor/
    capture.py              ← Playwright screenshot
    ocr.py                  ← EasyOCR text extraction
    diff.py                 ← text diff
    core.py                 ← main check logic
    scheduler.py            ← APScheduler
    storage.py              ← baseline files + SQLite change log
    notify.py               ← Telegram
  dashboard/
    app.py                  ← Flask dashboard
    templates/
      index.html
      site_form.html        ← Add/Edit with visual clip selector
      logs.html
  data/
    sites/<name>/
      baseline.png          ← last known screenshot (clipped)
      baseline_text.txt     ← OCR text of baseline
    changes.db              ← SQLite change history
    activity.log
    monitor.log
  .env                      ← credentials
  requirements.txt
```

## Commands

```bash
python run.py start   # start monitoring + dashboard (default)
python run.py check   # run one check of all sites and exit
```
