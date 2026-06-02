"""Unit tests for the shared browser runtime launch path (M1: resilience).

These tests fully fake the Playwright driver so no real Chromium is launched —
they verify the retry/classify/breadcrumb/healthcheck/stability-args logic in
isolation. Run from the repo root: `python3 -m pytest skills/_browser_runtime/`.
"""

from __future__ import annotations

import pytest

from skills._browser_runtime import runtime


# --- fakes -----------------------------------------------------------------


class TargetClosedError(Exception):
    """Stand-in whose class name matches the retryable signature exactly."""


class FakePage:
    def __init__(self) -> None:
        self.goto_urls: list[str] = []

    def goto(self, url: str, **_kw) -> None:
        self.goto_urls.append(url)


class FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def new_page(self) -> FakePage:
        return FakePage()

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    def new_context(self, **_kw) -> FakeContext:
        return FakeContext()

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    """Records launch kwargs and fails the first ``fail_times`` launches."""

    def __init__(self, *, fail_times: int = 0, exc_factory=None) -> None:
        self.calls = 0
        self.launch_kwargs: list[dict] = []
        self.fail_times = fail_times
        self.exc_factory = exc_factory or TargetClosedError

    def launch(self, **kwargs):
        self.calls += 1
        self.launch_kwargs.append(kwargs)
        if self.calls <= self.fail_times:
            raise self.exc_factory()
        return FakeBrowser()


class FakeDriver:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _Factory:
    """What the fake ``sync_playwright()`` returns; ``.start()`` → driver."""

    def __init__(self, chromium: FakeChromium) -> None:
        self._chromium = chromium

    def start(self) -> FakeDriver:
        return FakeDriver(self._chromium)


def _install_fake(monkeypatch, chromium: FakeChromium) -> None:
    monkeypatch.setattr(runtime, "sync_playwright", lambda: _Factory(chromium))
    monkeypatch.setattr(runtime, "_resolve_browser_channel", lambda: None)


@pytest.fixture(autouse=True)
def _fast_and_headed(monkeypatch):
    """No real sleeps; default to headed laptop unless a test forces headless."""
    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_BACKOFF_MS", "0")
    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_RETRIES", "3")
    monkeypatch.setattr(runtime, "_force_headless", lambda: False)


# --- retry / classification ------------------------------------------------


def test_launch_retries_then_succeeds(monkeypatch, capsys):
    chromium = FakeChromium(fail_times=1, exc_factory=TargetClosedError)
    _install_fake(monkeypatch, chromium)

    with runtime.launch_persistent("square") as (ctx, page):
        assert ctx is not None and page is not None

    assert chromium.calls == 2  # failed once, recovered on second
    err = capsys.readouterr().err
    assert "chromium launch failed (attempt 1/3)" in err
    assert "recovered on attempt 2/3" in err


def test_retry_backoff_sleeps_between_attempts(monkeypatch):
    chromium = FakeChromium(fail_times=1, exc_factory=TargetClosedError)
    _install_fake(monkeypatch, chromium)
    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_BACKOFF_MS", "200")
    slept: list[float] = []
    monkeypatch.setattr(runtime.time, "sleep", lambda s: slept.append(s))

    with runtime.launch_persistent("square"):
        pass

    assert chromium.calls == 2
    assert slept == [0.2]  # base backoff * 2**(attempt-1) for the first retry


def test_auth_class_error_is_not_retried(monkeypatch):
    class SquareLoginError(Exception):
        pass

    chromium = FakeChromium(fail_times=99, exc_factory=SquareLoginError)
    _install_fake(monkeypatch, chromium)

    with pytest.raises(SquareLoginError):
        with runtime.launch_persistent("square"):
            pass
    assert chromium.calls == 1  # non-retryable → no second attempt


def test_retries_exhausted_then_raises(monkeypatch):
    chromium = FakeChromium(fail_times=99, exc_factory=TargetClosedError)
    _install_fake(monkeypatch, chromium)

    with pytest.raises(TargetClosedError):
        with runtime.launch_persistent("square"):
            pass
    assert chromium.calls == 3  # exhausted the default 3 attempts


def test_body_exception_is_not_retried_and_captures_evidence(monkeypatch):
    chromium = FakeChromium(fail_times=0)
    _install_fake(monkeypatch, chromium)
    captured: dict = {}
    monkeypatch.setattr(
        runtime,
        "_capture_failure_evidence",
        lambda page, portal: captured.update(portal=portal),
    )

    class BodyError(Exception):
        pass

    with pytest.raises(BodyError):
        with runtime.launch_persistent("square"):
            raise BodyError()

    assert chromium.calls == 1  # launched once; the BODY error never re-launches
    assert captured.get("portal") == "square"


# --- stability args --------------------------------------------------------


def test_stability_args_absent_when_headed(monkeypatch):
    chromium = FakeChromium()
    _install_fake(monkeypatch, chromium)
    monkeypatch.setattr(runtime, "_force_headless", lambda: False)

    with runtime.launch_persistent("square", headed=True):
        pass

    args = chromium.launch_kwargs[0]["args"]
    assert "--disable-dev-shm-usage" not in args
    assert "--no-sandbox" not in args


def test_stability_args_present_when_headless(monkeypatch):
    chromium = FakeChromium()
    _install_fake(monkeypatch, chromium)
    monkeypatch.setattr(runtime, "_force_headless", lambda: True)

    with runtime.launch_persistent("square", headed=True):
        pass

    args = chromium.launch_kwargs[0]["args"]
    assert "--disable-dev-shm-usage" in args
    assert "--no-sandbox" in args
    assert "--disable-gpu" in args


# --- healthcheck -----------------------------------------------------------


def test_healthcheck_healthy(monkeypatch):
    chromium = FakeChromium(fail_times=0)
    _install_fake(monkeypatch, chromium)
    assert runtime.browser_healthcheck() is True


def test_healthcheck_unhealthy_uses_retry_path(monkeypatch):
    chromium = FakeChromium(fail_times=99, exc_factory=TargetClosedError)
    _install_fake(monkeypatch, chromium)
    assert runtime.browser_healthcheck() is False
    assert chromium.calls == 3  # went through the same retry path


def test_healthcheck_post_launch_goto_failure(monkeypatch):
    chromium = FakeChromium(fail_times=0)
    _install_fake(monkeypatch, chromium)

    class _BoomPage(FakePage):
        def goto(self, url, **_kw):
            raise RuntimeError("renderer gone")

    monkeypatch.setattr(FakeContext, "new_page", lambda self: _BoomPage())
    assert runtime.browser_healthcheck() is False


# --- pure helpers ----------------------------------------------------------


def test_is_retryable_launch_error_matches_infra_only():
    assert runtime._is_retryable_launch_error(TargetClosedError())
    assert runtime._is_retryable_launch_error(
        Exception("BrowserType.launch: Target page, context or browser has been closed")
    )
    timeout = type("TimeoutError", (Exception,), {})("Timeout 30000ms exceeded during launch")
    assert runtime._is_retryable_launch_error(timeout)

    class SquareLoginError(Exception):
        pass

    assert not runtime._is_retryable_launch_error(SquareLoginError("bad password"))
    assert not runtime._is_retryable_launch_error(ValueError("selector not found"))


def test_config_helpers(monkeypatch):
    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_RETRIES", "5")
    assert runtime._launch_retries() == 5
    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_RETRIES", "0")
    assert runtime._launch_retries() == 1  # floored at 1
    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_RETRIES", "bogus")
    assert runtime._launch_retries() == 3  # default on parse error

    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_BACKOFF_MS", "250")
    assert runtime._launch_backoff_ms() == 250
    monkeypatch.setenv("BHAGA_BROWSER_LAUNCH_BACKOFF_MS", "bogus")
    assert runtime._launch_backoff_ms() == 1000


def test_launch_args_helper_gating(monkeypatch):
    monkeypatch.setattr(runtime, "_force_headless", lambda: False)
    headed_args = runtime._launch_args(headed=True)
    assert "--disable-dev-shm-usage" not in headed_args

    headless_args = runtime._launch_args(headed=False)
    assert "--disable-dev-shm-usage" in headless_args

    # force_headless overrides a headed request (container safety net)
    monkeypatch.setattr(runtime, "_force_headless", lambda: True)
    forced = runtime._launch_args(headed=True)
    assert "--no-sandbox" in forced


# --- trace_step: step-by-step screenshot trace -----------------------------


class _TracePage:
    """Records screenshot() calls and exposes a url, like a Playwright Page."""

    def __init__(self, url: str = "https://app.squareup.com/login") -> None:
        self.url = url
        self.shots: list[dict] = []

    def screenshot(self, *, path: str, full_page: bool = False) -> None:
        self.shots.append({"path": path, "full_page": full_page})
        # touch the file so any downstream existence check passes
        import pathlib as _pl

        _pl.Path(path).write_bytes(b"\x89PNG\r\n")


def test_trace_step_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("BHAGA_TRACE_SCREENSHOTS", raising=False)
    page = _TracePage()
    assert runtime.trace_step(page, "landing") is None
    assert page.shots == []  # no screenshot taken when tracing is off


def test_trace_step_noop_when_page_none(monkeypatch):
    monkeypatch.setenv("BHAGA_TRACE_SCREENSHOTS", "1")
    assert runtime.trace_step(None, "landing") is None


def test_trace_step_uploads_full_page_with_seq_and_label(monkeypatch, tmp_path):
    monkeypatch.setenv("BHAGA_TRACE_SCREENSHOTS", "1")
    monkeypatch.setattr(runtime, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(runtime, "_evidence_refresh_date", lambda: __import__("datetime").date(2026, 5, 31))

    uploads: list[dict] = []

    def _fake_upload(local_path, *, refresh_date, category):
        uploads.append(
            {"name": local_path.name, "category": category, "date": refresh_date.isoformat()}
        )
        return f"gs://sandbox-bucket/{refresh_date.isoformat()}/{category}/{local_path.name}"

    # Patch the REAL module's upload_file (the runtime does
    # `from agents.bhaga.scripts import gcs_cache`, which binds the already-imported
    # module attribute — so swapping sys.modules is order-dependent and would also
    # risk a real GCS write). Patching the function in place is robust + side-effect-free.
    from agents.bhaga.scripts import gcs_cache as _gcs_cache

    monkeypatch.setattr(_gcs_cache, "upload_file", _fake_upload)

    # reset the module-level sequence so the assertion on NN- is deterministic
    monkeypatch.setattr(runtime, "_TRACE_SEQ", 0)

    page = _TracePage()
    uri = runtime.trace_step(page, "Login Email Screen!")

    assert page.shots and page.shots[0]["full_page"] is True
    assert uploads and uploads[0]["category"] == "trace"
    assert uploads[0]["date"] == "2026-05-31"
    # filename: trace-01-login-email-screen-<ts>.png (label slugified, seq zero-padded)
    assert uploads[0]["name"].startswith("trace-01-login-email-screen-")
    assert uri.startswith("gs://sandbox-bucket/2026-05-31/trace/")


def test_trace_step_never_raises_on_screenshot_error(monkeypatch, tmp_path):
    monkeypatch.setenv("BHAGA_TRACE_SCREENSHOTS", "1")
    monkeypatch.setattr(runtime, "EVIDENCE_DIR", tmp_path)

    class _BoomPage:
        url = "https://x"

        def screenshot(self, **_kw):
            raise RuntimeError("driver gone")

    # a tracing hiccup must never break the scrape
    assert runtime.trace_step(_BoomPage(), "boom") is None
