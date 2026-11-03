# PPE Compliance Monitor – System Design

Version 1.0 · Napino Auto, SMT Gallery 2 · single camera · Windows 11 laptop
(Intel i3-1115G4, 8 GB RAM, Intel UHD graphics, no discrete GPU).

---

## 1. Purpose
Real-time check of personnel at the assembly-shop entrance for the mandatory PPE:

| Item | Rule |
|---|---|
| Coat | any plain block colour (white, light blue, turquoise …); not a shirt, t-shirt, patterned shirt or jacket |
| Cap | **white** cap (blue hairnets / light-blue caps do not count) |
| Floaters | light-blue floaters |

Status per person: 3 items = **FULL** (green) · 2 = **PARTIAL** (amber) · 0–1 = **NO PPE** (red) ·
not enough evidence yet = **VERIFYING** (grey). The seated security guard and reflections in the
glass window are ignored. Every PARTIAL / NO PPE visit is captured as an *incident* (annotated
screenshot) and emailed.

## 2. Architecture

```
 Hikvision NVR ──RTSP/TCP──► VideoSource (thread "camera")
                               newest frame only, normalised to 1920x1080, auto-reconnect
                                   │
               ┌───────────────────┴──────────────────────┐
               ▼                                          ▼
   Monitor.process()  (thread "analysis",         Monitor._render_loop (thread "render", ~10/s,
                       6/s active, 1.5/s idle)    only while a dashboard viewer is connected)
   PersonDetector (YOLO26s, OpenVINO)             overlay.draw_person / draw_alerts on each frame
   Tracker (IoU, two-stage)                       (boxes extrapolated with track velocity)
   PersonRegistry (guard, reflections, stitching) → JPEG → /stream.mjpg
   Scorer (cap, floaters) + CoatClassifier (CLIP)
   Person (smoothing, lock, visit verdict)
               │ visit finished, verdict PARTIAL/NONE
               ▼
   IncidentStore (SQLite + JPEG)  ──► EmailNotifier (thread "email", Gmail SMTP)
               ▲
   RetentionJob (thread "retention", hourly purge > retention_days)

   Dashboard (Flask app served by waitress, http://localhost:8080):  /  /stream.mjpg  /api/status  /api/incidents
```

All loops are wrapped so an exception is logged and the loop restarts. The monitor runs only while
`START_PPE_MONITOR.bat`'s window is open: there is no auto-start and no automatic process restart;
Ctrl+C (or Ctrl+Break) in that window stops it cleanly (`run.py` catches it and exits).

**Load control** (settings.toml `[processing]`, for slow PCs):

| Measure | Setting | Effect |
|---|---|---|
| Process priority *below normal* | `priority` | Windows serves the user, browser and other programs first |
| Capped AI threads on CPU | `cpu_threads` (0 = half the logical cores) | the AI never occupies every core |
| Capped OpenCV threads | `opencv_threads` (2) | resizing / colour conversion stay light |
| GPU work at low queue priority / throttle | (always) | screen and other GPU users stay smooth |
| Idle rate | `idle_analysis_fps` (1.5), `motion_threshold` | empty, still corridor analysed 4x less; any person or movement → full `analysis_fps` for ≥ 3 s |
| Overlay video drawn only when watched | (always) | no drawing / JPEG encoding while no dashboard tab shows the feed; the page pauses the feed when its tab is hidden |
| Hardware video decoding | `hw_decode` (off) | decode on the graphics chip; lighter on some PCs, heavier on others – `CHECK_SYSTEM.bat` measures both |
| Recordings read at ~25 fps | (file source only) | 50/60 fps demo videos are not fully decoded |

## 3. Source layout

```
live_environment\
  START_PPE_MONITOR.bat        launcher: runs the monitor in its window until Ctrl+C
  app\run.py                   entry point: logging, config, single-instance check, start monitor + web
  app\ppe_live\
    config.py                  loads config\settings.toml (+ optional override), dashboard preferences; paths
    credentials.py             NVR / sender sign-in: DPAPI-encrypted, expiring (see §7)
    video_source.py            RTSP / file capture thread
    inference.py               OpenVINO models (GPU -> CPU fallback): PersonDetector, CoatClassifier
    tracker.py                 lightweight ByteTrack-style tracker (no PyTorch dependency)
    ppe_rules.py               Scorer (pixel rules), Person (evidence, smoothing, lock, visit verdict),
                               PersonRegistry (guard / reflection exclusion, guard hand-off, track stitching)
    monitor.py                 orchestration: analysis + render loops, incident creation, status for UI
    overlay.py                 drawing (identical look to the proof-of-concept videos)
    incidents.py               SQLite store, screenshots, retention job
    notifier.py                email composition (HTML + inline image + attachment) and sending
    dashboard.py, web\         Flask routes and the dashboard: Live tab + Settings tab (HTML/CSS/JS, no CDN)
    log.py                     console + rotating log file
  config\settings.toml         every tunable value (commented)
  config\demo_override.toml    developer only: plays a recorded video, writes emails to data\outbox
  models\                      OpenVINO IR models + coat head (see §5)
  runtime\python\              embeddable CPython 3.11.9 + packages (see §8)
  tools\                       diagnostics (.bat + .py), replay_check.py for developers
  build\                       how runtime and models were produced (developer machine only)
```

Run-time folders live **next to** the program folder (settings.toml → `[storage]`, relative to
`live_environment`), so a new version can replace `live_environment` without touching them.
Client PC layout:

```
D:\NapinoPPE\
  live_environment\     program (this package)
  screenshots\          <YYYY-MM-DD>\HHMMSS_person<N>_<status>.jpg
  data\                 incidents.db, access.dat (encrypted sign-in), preferences.json, camera_test.jpg
  logs\                 ppe_monitor.log (5 x 5 MB rotating)
```
No path is hard-coded: the `.bat` files use their own location (`%~dp0`), the app resolves the
`[storage]` folders against its own folder. The resolved folders are printed at start-up (log) and by
`CHECK_SYSTEM.bat`.

## 4. Processing pipeline (per analysed frame)
1. **Detect** persons: YOLO26s, 640x640 letterbox, NMS-free output `[1,300,6]` (x1,y1,x2,y2,score,class).
2. **Track**: high-score detections matched by IoU ≥ 0.25 to predicted boxes, then low-score
   detections (≥ 0.12) to the remaining tracks; new tracks need score ≥ 0.40 and must not overlap an
   existing track (IoU > 0.6, the detector sometimes returns duplicate boxes); tracks die after 1.5 s unseen;
   a track is shown after 2 hits.
3. **Registry** (`PersonRegistry`):
   - *exclusions* – box height < `min_show_height`; seated box in `seat_zone`; centre inside `reflection_x`.
   - *guard* – a person seated (height ≤ `guard_seated_max_height`) in `guard_zone` for
     `guard_confirm_seconds` is the guard and is hidden for the rest of the track.
   - *guard hand-off* – the tracker changes id when the guard sits down / stands up. A walking track that
     ends within 3 s / 260 px of a guard sitting down, or starts within 3 s / 260 px of a guard leaving the
     chair, is also the guard (no incident).
   - *stitching* – a new track that starts within 2 s where a person was lost, with a similar height
     (0.65–1.55x) and centre within 0.6 x height, continues that person (same number, same evidence).
4. **Score** people whose box height ≥ `min_judge_height`:
   - *cap*: median HSV of the head-top patch (rows 2–8 %, columns 35–65 % of the box):
     p = σ((`cap_max_saturation` − S)/2.5) · σ((V − `cap_min_brightness`)/6), defaults 24 / 60 (white caps
     measure S ≈ 6–22; blue hairnets / light-blue caps S ≈ 50–100; dark hair V < 60). Skipped if the head is cut by the frame top.
   - *floaters*: fraction of pixels in the bottom 10 % of the box with H 105–140, S ≥ 40, V ≥ 70;
     soft ramp around `floater_min_fraction`. Skipped if the feet are cut by the frame bottom.
   - *coat*: crop rows 12–78 % of the box → CLIP ViT-B/32 image embedding → logistic head
     p = σ(12 · (w·f + b)); limited to `coat_checks_per_second` per person (heaviest step).
5. **Smooth** (`Person.observe`): weighted mean over the last `smoothing_seconds`
   (weight = min(1, height/350)), hysteresis on 0.55 / 0.40. An item never observed counts as missing.
6. **Lock**: FULL is frozen once it has held for `lock.confirm_seconds` while the person is ≥
   `lock.min_height` px tall and every item's mean is ≤ 0.25 or ≥ 0.75. Only FULL is locked.
7. **Visit verdict** (when the person leaves, after the 2 s stitching wait + 4 s guard grace, or after
   `max_observe_seconds`): per item the weighted mean of all observations taken at ≥
   `min_close_height` px (falls back to all observations if fewer than 3). PARTIAL / NONE → incident,
   provided the person was judged for ≥ `min_judged_seconds`. The screenshot is the frame where the
   person was largest, re-drawn with the visit verdict.
8. **Email gate**: an incident is emailed only if the person was observed at ≥ `min_close_height` px at
   least `email_min_close_observations` times (default 3). Visits judged only at a distance (people who
   stay at the far door / turnstiles) are logged on the dashboard as *seen only at a distance* – pixel
   rules are unreliable at ~150–200 px person height, so this keeps the emails trustworthy.

All pixel values refer to the 1920x1080 canvas (frames of any resolution are scaled to it).

## 5. Models
| File | What | Size | Origin |
|---|---|---|---|
| `models\person_detector\yolo26s.xml/.bin` | person detector (COCO class 0), FP16 | 19 MB | Ultralytics YOLO26s → OpenVINO |
| `models\person_detector\yolo26m.xml/.bin` | more accurate alternative (`detector_model = "yolo26m"`), ~3x slower | 41 MB | Ultralytics YOLO26m |
| `models\coat_classifier\clip_vitb32_visual.xml/.bin` | CLIP image encoder, L2-normalised 512-d output, FP16 | 176 MB | OpenAI CLIP ViT-B/32 |
| `models\coat_classifier\coat_head.npz` | coat-vs-shirt linear head (w, b, scale) | 3 KB | trained on 106 labelled crops of 20 people from the 16-Oct-2026 recording (leave-one-person-out accuracy 0.82 per frame) |

Re-create with `build\export_models.py`. The coat head training code and labelled crops are in the
proof-of-concept folder (`..\ppe_monitor\train_heads.py`, `train_crops\`).

**Measured speed** (OpenVINO 2026.4): YOLO26s 66 ms (4 CPU threads) / 120 ms (2 threads); CLIP 60 ms
(2 threads). On the client i3-1115G4 the Intel UHD GPU is used when available; otherwise CPU. Run
`tools\CHECK_SYSTEM.bat` on the client to measure and set `analysis_fps` accordingly.

## 6. Incidents and email
- Table `incidents` in `data\incidents.db` (schema in `incidents.py`); screenshots in
  `screenshots\YYYY-MM-DD\HHMMSS_person<N>_<status>.jpg` (the `image` column is relative to `screenshots\`).
- Email states: `pending → sent`, `failed` (retried with back-off up to `retry_attempts`),
  `disabled`, `not_configured` (sender not signed in), `no_recipients`, `rate_limited` (`max_per_hour` reached),
  `far_only` (logged, not emailed – see §4.8).
- Gmail SMTP: `smtp.gmail.com:587` STARTTLS (or 465 SSL) with a Google **App password**
  (requires 2-Step Verification on the sender account). Message = text + HTML (inline screenshot) +
  JPEG attachment; recipients from the dashboard (first = To, rest = Cc); subject template `{camera}`, `{time}`.
- Retention: rows and day folders older than `retention_days` are deleted at start and hourly.

## 7. Configuration
`config\settings.toml` – every section is commented. Key groups: `[camera]` (stream, default NVR
address / user / channel for the sign-in form), `[processing]` (device, model, analysis rate), `[ppe]`
(thresholds), `[lock]`, `[zones]` (guard chair, reflections), `[incidents]`, `[email]` (SMTP server,
default sender, default recipients), `[security]` (`credential_days`), `[dashboard]`.

**Managed on the dashboard (Settings tab), never in a config file:**

| What | Stored in | Notes |
|---|---|---|
| Alert recipients | `data\preferences.json` | validated, de-duplicated; overrides `[email] recipients` |
| NVR address / user / channel, sender address | `data\preferences.json` | non-secret; pre-fills the forms |
| NVR password, sender App password | `data\access.dat` | Windows DPAPI (current user + app entropy); unreadable on any other PC / account |

Sign-in flow: the dashboard posts the password once; the server verifies it (NVR: opens the RTSP stream
and reads a frame; email: SMTP login without sending) and only then stores it encrypted. Passwords are
never returned by any API, never logged (RTSP URLs are logged as `user:****`), and expire after
`credential_days` (default 30) – the record is deleted and the dashboard shows *Sign-in required*.
While signed out the camera loop waits and new incidents are logged with email state `not_configured`.
All POST requests must carry the `X-PPE-Dashboard` header and come from the dashboard's own origin, so
other web pages open in the browser cannot change settings. The server listens on 127.0.0.1 only.
RTSP URL built as `rtsp://user:pass@IP:554/Streaming/Channels/<channel>0<1|2>` (Hikvision: 1 = main, 2 = sub).
Override files (`--config file.toml`) replace individual keys – used for the demo mode.

## 8. Runtime and packaging
- `runtime\python` = official embeddable CPython 3.11.9 + pinned wheels (`build\requirements.txt`:
  numpy, opencv-python-headless (FFmpeg for RTSP), openvino, pillow, flask, waitress) + app-local MSVC
  runtime DLLs. Built by `build\build_runtime.ps1`. No installer, no admin rights, no internet needed
  on the client (internet only for sending email).
- The embeddable interpreter ignores `PYTHONPATH` and does not add the script folder to `sys.path`;
  `run.py` and the tools insert their paths explicitly.
- No auto-start: the monitor is started by hand with `START_PPE_MONITOR.bat` and stopped with Ctrl+C.
- Client package: `build\make_package.ps1 -OutDir <folder>` creates `NapinoPPE_<date>.zip` + `.sha256`; it
  leaves out run-time data, logs, caches and developer-only files (`build\`, demo overrides, replay tool).
- The sign-in is encrypted per Windows user, so the monitor must be started from the same Windows account
  that entered it on the dashboard.
- Fonts: Segoe UI / Segoe MDL2 from `C:\Windows\Fonts` (fallback to a default font if missing).

## 9. Developer workflow
- Run on a recording (developer copy): `runtime\python\python.exe app\run.py --config config\demo_override.toml`
  – set `PPE_DATA_DIR` to a scratch folder to keep test incidents out of `data\`.
- Regression check without UI/emails: `runtime\python\python.exe tools\replay_check.py <video> [--start s --end s --cpu]`
  (writes to `data_replay\`). Compare incidents before/after any rule change.
- Logs: `logs\ppe_monitor.log` (5 x 5 MB rotating).

## 10. Known limitations / next steps
- Coat vs plain light shirt is the weakest check (CLIP linear probe, ~0.8 per-frame accuracy; visit-level
  averaging helps). A dedicated PPE detector trained on a few hundred labelled frames from this camera
  would improve all three items.
- The guard is recognised by the chair; if the guard walks the corridor without passing the chair they are
  judged like anyone else.
- People who never come close (box < `min_judge_height`) are never judged and never raise incidents.
- Thresholds were calibrated on a screen recording of the NVR web client; re-check colours / zones on the
  direct RTSP stream (`TEST_CAMERA.bat`, `replay_check.py`).
- Single camera; multi-camera would run one `Monitor` per camera (VideoSource/Registry are per camera).
