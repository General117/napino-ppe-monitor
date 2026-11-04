# Napino Auto – PPE Compliance Monitor (live)

Watches the **SMT Gallery 2** camera on the Hikvision NVR, checks every person for the required PPE
(**ESD Apron**, **ESD Cap**, **ESD Slippers**), shows the annotated live feed at **http://localhost:8080**,
and emails a screenshot of every possible non-compliance.

Everything needed is inside this folder (Python, AI models, settings). Nothing has to be installed.
No password is stored in any file: sign-in is done on the dashboard and kept encrypted on this PC
for 30 days.

---

## Getting the program from GitHub (private repository)

Needs **Git for Windows** (https://git-scm.com/download/win – includes Git LFS for the large model files).

```
cd /d D:\NapinoPPE
git clone https://github.com/General117/napino-ppe-monitor.git live_environment
```
- `D:\NapinoPPE\live_environment` must not exist yet (or be empty) – remove an empty one first.
- A GitHub sign-in window opens the first time (private repository).
- **Update later:** stop the monitor (Ctrl+C), then `cd /d D:\NapinoPPE\live_environment` and `git pull`.
  Screenshots, data, sign-in and recipients (next to `live_environment`) are not touched.

## First-time setup on the client PC

1. **Get the program** into `D:\NapinoPPE\live_environment\` (git clone above, or extract a package zip).
   The folders `screenshots`, `data` and `logs` next to it are used automatically (created if missing).
2. Double-click `tools\CHECK_SYSTEM.bat` – checks graphics, speed, fonts, disk.
3. Double-click `START_PPE_MONITOR.bat` – the dashboard opens in the browser.
4. Dashboard → **Settings**:
   - **Camera (NVR)** – NVR address, user name, password, channel → **Connect**
   - **Sender email** – Gmail address + **App password** → **Sign in** → **Send test email**
   - **Alert recipients** – add / remove → **Save recipients**
5. Double-click `tools\TEST_CAMERA.bat` – saves `D:\NapinoPPE\data\camera_test.jpg`; check the guard-chair and
   window zones sit in the right place.
6. Windows power settings: **Sleep = Never** (plugged in).

**Every 30 days** the sign-ins expire: the dashboard shows *Sign-in required* – enter them again in Settings.

### Gmail App password (sender)
On the sender's Google account: turn on 2-Step Verification, open
https://myaccount.google.com/apppasswords, create one named "PPE Monitor", and enter the 16-letter
code in Settings → Sender email. (For a Workspace account the admin must allow App passwords.)

## Daily use
- **Start:** double-click `START_PPE_MONITOR.bat`. Keep its window open (it can be minimised).
- **Stop:** press **Ctrl+C** in that window (answer **Y** if asked *Terminate batch job*), or close the window.
- Dashboard: **http://localhost:8080** – live feed, today's counts, recent incidents (click to enlarge).
- Camera drops reconnect automatically. Incidents older than 30 days are deleted automatically.
- The monitor runs at low priority and slows down when the corridor is empty, so the PC stays usable.

## Where things are
```
D:\NapinoPPE\
  live_environment\   the program (replace this folder to update - nothing below is touched)
  screenshots\        incident screenshots, one folder per day (YYYY-MM-DD)
  data\               incident log, encrypted sign-in, recipient list
  logs\               ppe_monitor.log - send this when reporting a problem
```
Inside `live_environment`: `config\settings.toml` (technical settings, incl. these folder locations
under `[storage]`), `docs\SYSTEM_DESIGN.md` (technical documentation).

## Troubleshooting
| Symptom | Fix |
|---|---|
| *Sign-in required* banner | Settings → sign in again (expired after 30 days or never entered) |
| Camera *Offline* | PC on the factory network? NVR address / channel right? RTSP enabled on the NVR? Run `tools\TEST_CAMERA.bat` |
| Sender sign-in: *Cannot reach smtp.gmail.com* | the network blocks outgoing email – ask Napino IT to allow TCP 587 to smtp.gmail.com |
| Sender sign-in: *rejected* | use the 16-letter App password, not the normal Google password |
| PC slow / video lagging | run `tools\CHECK_SYSTEM.bat` and follow its advice (`hw_decode`, `analysis_fps` in `config\settings.toml`) |
| *Port 8080 is already in use* | the monitor is already running in another window – use that one, or close it first |
