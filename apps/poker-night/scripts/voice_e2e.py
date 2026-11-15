"""Three phones join voice at one table (Chromium fake mic) and must hear each other, mute, leave,
survive a killed tab and rejoin. Run against a local build: `npx next start -p 3123 -H 127.0.0.1`, then
`python3 scripts/voice_e2e.py` (needs `pip install playwright && playwright install chromium`).
TONE=/path/to.wav feeds a continuous tone instead of Chromium's default beep."""
import json, os, sys, time, urllib.request
from playwright.sync_api import sync_playwright

B = os.environ.get("B", "http://127.0.0.1:3123")
ART = os.environ.get("ART", "/tmp")

def post(path, body, tok=None):
    h = {"content-type": "application/json", **({"x-player-token": tok} if tok else {})}
    return json.load(urllib.request.urlopen(urllib.request.Request(B + path, json.dumps(body).encode(), h)))

c = post("/api/rooms", {"name": "Harry", "avatar": "🦁", "startingChips": 1000, "smallBlind": 10, "bigBlind": 20, "dealMode": "app"})
rid, host = c["roomId"], c["token"]
ron = post(f"/api/rooms/{rid}/join", {"name": "Ron", "avatar": "🐺"})["token"]
herm = post(f"/api/rooms/{rid}/join", {"name": "Hermione", "avatar": "🦊"})["token"]
post(f"/api/rooms/{rid}/action", {"kind": "start"}, host)

STATE = """() => ({
  badges: [...document.querySelectorAll('.mic-badge')].map(b => b.textContent.trim()),
  speaking: [...document.querySelectorAll('.seat2')].filter(s => s.querySelector('.avatar-wrap.speaking')).map(s => s.querySelector('.nm')?.textContent),
  audios: [...document.querySelectorAll('audio')].filter(a => a.srcObject && !a.paused).length,
  mic: document.querySelector('.btn.icon.mic')?.getAttribute('aria-label'),
})"""

fails = []
def check(name, ok, detail):
    print(("ok   " if ok else "FAIL ") + name, detail)
    if not ok: fails.append(name)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", *([f"--use-file-for-fake-audio-capture={os.environ['TONE']}"] if os.environ.get("TONE") else []), "--autoplay-policy=no-user-gesture-required"])
    pages = {}
    sdp_sizes = []
    def watch(pg):
        def on_req(r):
            if r.url.endswith("/voice") and r.method == "POST":
                body = json.loads(r.post_data or "{}")
                if body.get("action") == "signal":
                    sdp_sizes.append(len(body["sdp"]))
        pg.on("request", on_req)
    def open_phone(name, tok):
        ctx = browser.new_context(viewport={"width": 430, "height": 860}, device_scale_factor=2, is_mobile=True, has_touch=True,
                                  permissions=["microphone"], record_video_dir=f"/tmp/voice-video/{name}", record_video_size={"width": 430, "height": 860})
        ctx.add_init_script(f"localStorage.setItem('poker-night:{rid}', '{tok}')")
        pg = ctx.new_page()
        watch(pg)
        pg.goto(f"{B}/t/{rid}")
        pg.wait_for_selector(".felt2")
        return pg
    for name, tok in [("harry", host), ("ron", ron), ("hermione", herm)]:
        pages[name] = open_phone(name, tok)
    time.sleep(1.5)
    pages["harry"].screenshot(path=f"{ART}/voice-1-before-join.png")
    for name in ["harry", "ron", "hermione"]:
        pages[name].locator(".btn.icon.mic").tap()
        time.sleep(0.8)
    time.sleep(6)
    for name, pg in pages.items():
        s = pg.evaluate(STATE)
        check(f"{name} sees 3 people on the mic", len(s["badges"]) == 3, s["badges"])
        check(f"{name} is playing 2 remote audio streams", s["audios"] == 2, s["audios"])
    # The fake mic beeps about once a second; sample until every seat has glowed.
    seen = {n: set() for n in pages}
    for _ in range(40):
        for name, pg in pages.items():
            seen[name] |= set(pg.evaluate(STATE)["speaking"])
        time.sleep(0.12)
    for name in pages:
        others = {"harry": {"Ron", "Hermione"}, "ron": {"Harry", "Hermione"}, "hermione": {"Harry", "Ron"}}[name]
        check(f"{name} hears both others (speaking glow from received audio)", others <= seen[name], sorted(seen[name]))
    # Catch a frame with a glow for the screenshot.
    for _ in range(30):
        if pages["harry"].evaluate(STATE)["speaking"]:
            break
        time.sleep(0.05)
    pages["harry"].screenshot(path=f"{ART}/voice-2-everyone-talking.png")

    pages["ron"].locator(".btn.icon.mic").tap()
    time.sleep(3)
    s = pages["harry"].evaluate(STATE)
    check("Harry sees Ron muted", "🔇" in s["badges"], s["badges"])
    ron_glow = False
    for _ in range(25):
        ron_glow |= "Ron" in pages["harry"].evaluate(STATE)["speaking"]
        time.sleep(0.12)
    check("muted Ron no longer glows for Harry", not ron_glow, "")
    pages["ron"].screenshot(path=f"{ART}/voice-3-ron-muted.png")

    pages["hermione"].locator(".voice-leave").tap()
    time.sleep(3)
    s = pages["harry"].evaluate(STATE)
    check("Hermione left: Harry sees 2 on the mic and plays 1 stream", len(s["badges"]) == 2 and s["audios"] == 1, s)
    s = pages["hermione"].evaluate(STATE)
    check("Hermione's page stopped all audio", s["audios"] == 0 and s["mic"] == "Join voice chat", s)
    pages["hermione"].screenshot(path=f"{ART}/voice-4-hermione-left.png")

    # Resilience: Ron's tab dies without leaving (phone locked / app killed), then he comes back.
    pages["ron"].context.close()
    t0 = time.time()
    while time.time() - t0 < 30:
        s = pages["harry"].evaluate(STATE)
        if len(s["badges"]) == 1 and s["audios"] == 0:
            break
        time.sleep(0.5)
    check("Ron's dead tab drops out of Harry's voice", len(s["badges"]) == 1 and s["audios"] == 0, f"{s} after {time.time() - t0:.1f}s")
    pages["ron"] = open_phone("ron2", ron)
    time.sleep(1)
    pages["ron"].locator(".btn.icon.mic").tap()
    t0 = time.time()
    heard = set()
    while time.time() - t0 < 20:
        s = pages["harry"].evaluate(STATE)
        heard |= set(s["speaking"])
        if s["audios"] == 1 and "Ron" in heard:
            break
        time.sleep(0.15)
    check("Ron rejoins and Harry hears him again", s["audios"] == 1 and len(s["badges"]) == 2 and "Ron" in heard, f"{s} after {time.time() - t0:.1f}s")
    pages["harry"].screenshot(path=f"{ART}/voice-5-ron-rejoined.png")
    check("every offer/answer SDP stays under the 9000-char cap", sdp_sizes and max(sdp_sizes) < 9000, f"{len(sdp_sizes)} signals, max {max(sdp_sizes or [0])} chars")
    time.sleep(1)
    for name, pg in pages.items():
        if name != "hermione" or not pg.is_closed():
            pg.context.close()
    browser.close()
print(f"\n{len(fails)} failures")
sys.exit(1 if fails else 0)
