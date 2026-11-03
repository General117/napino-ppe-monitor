# Context for AI assistants / developers (state on 2026-11-03)

**Project:** live PPE compliance monitor for Napino Auto, camera "SMT Gallery 2" on a Hikvision NVR.
PPE = coat (any plain colour) + WHITE cap + light-blue floaters. 3 = full (green), 2 = partial (amber),
0-1 = none (red). Seated security guard + glass-window reflections ignored. Full PPE locks once confirmed.
Partial/none visit -> screenshot + Gmail SMTP alert. Dashboard http://localhost:8080 (Flask + waitress).

**Read first:** `README.md` (operator steps), `docs/SYSTEM_DESIGN.md` (full technical design).

## Machines
- **Dev PC:** source `D:\Renata\Napino Auto\live_environment` (git work tree). D: nearly full ->
  git dir at `E:\RenataBackup\NapinoPPE\repo\napino-ppe-monitor.git`, packages in
  `E:\RenataBackup\NapinoPPE\dist`, scratch/test data on E:. Never write big files to C:/D:.
  Proof-of-concept (offline video, training crops, coat-head training) in `D:\Renata\Napino Auto\ppe_monitor`.
- **Client PC:** HP 250 G8, i3-1115G4, 8 GB, Intel UHD, Win 11, Wi-Fi, domain napino.dom, reached via
  UltraViewer. Layout `D:\NapinoPPE\{live_environment, screenshots, data, logs}` (settings.toml [storage]
  uses `../` paths). Installed by `git clone` of the repo below.
- **Repo:** private https://github.com/General117/napino-ppe-monitor (main). Large files via Git LFS.
  `gh` CLI is installed on dev PC (account General117) but does not work inside the work tree
  (separate git dir) - use plain git. Package zip: `build\make_package.ps1 -OutDir E:\RenataBackup\NapinoPPE\dist`.

## Decisions (user-confirmed)
- No passwords in files: NVR + sender App password entered on dashboard Settings, stored DPAPI-encrypted
  (`data\access.dat`), expire after 30 days. Recipients editable on dashboard (`data\preferences.json`).
- Sender: kartik.mishra@nexren.ai (Gmail App password works - verified). Recipients default:
  tamanna@napino.com (To), anand@, sanjay.vashistha@, anand.sharma@napino.com (Cc).
- No auto-start/restart: client runs `START_PPE_MONITOR.bat`, stops with Ctrl+C.
- Run light on slow PCs: below-normal priority, capped threads, idle analysis rate, overlay drawn only
  when the dashboard is open, `hw_decode` off by default.
- Emails only when the person was seen close (`email_min_close_observations`); far-only = logged only.
- Never paste credentials into chat; the user types them on the dashboard.

## NVR facts
IP 172.16.16.22 (office LAN; LAN1 10.0.0.202 is camera side), HTTP 80, RTSP 554, server 8000 - all
reachable from client. User `Servlens`. SMT Gallery 2 = 13th camera in the NVR list -> RTSP
`/Streaming/Channels/1301`. NVR web UI needs Edge IE mode (plugin); irrelevant for RTSP.

## OPEN ISSUE (next step)
Dashboard "Connect" fails. `tools\DIAGNOSE_CAMERA.bat` on the client: ports 80/554/8000 open, but every
RTSP URL (1301, 1302, 4501) returns **401 Unauthorized**; 'ch' URL and channel 1 gave no answer.
Likely causes, to check in that order:
1. Password typed wrongly at the prompt (re-run the tool carefully).
2. `Servlens` lacks the RTSP/remote live-view right: NVR web -> Configuration -> System -> User
   Management -> Servlens -> Remote Live View (and channel 13) enabled.
3. NVR RTSP authentication mode: Configuration -> System -> Security -> Authentication ->
   RTSP Authentication -> try "digest/basic".
4. Too many failed attempts can lock the user for ~30 min (Security -> "Enable Illegal Login Lock").
5. If still 401: ask the NVR admin for a dedicated user with live-view rights, or test with VLC
   (`rtsp://user:pass@172.16.16.22:554/Streaming/Channels/1301`) to rule out the code.
Once a URL works, nothing in the code should need changing (channel field = 13).

## Validation done
Full 22-min recording replay (`tools\replay_check.py`): 21-22 emailed incidents, all correct, 0 false
emails, guard ignored. Fresh git clone on E: passed `tools\CHECK_SYSTEM.bat`.
