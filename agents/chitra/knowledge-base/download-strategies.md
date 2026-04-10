# Document Download Strategies

Generalizable patterns for downloading tax documents from web portals.
Learned and verified during 2026-04-05/06 execution sessions.

## Download Methods

### 1. Direct Link Download
**Portals**: Schwab, E-Trade, Robinhood, Wells Fargo, Chase, Homebase, San Mateo County, Fort Bend County
**How it works**: Document page has a download link/button that directly returns a PDF.
**Playwright**: Click the link — Playwright's download handler captures the file to `extracted/downloads/`.
**Pitfall**: Some sites open PDF inline (Content-Disposition: inline). In that case, use fetch-based download (method 4).

### 2. Viewer Download Button (Yardi Platform)
**Portals**: MH Capital (invportal.com), BCGK InvestorCafe (investorcafe.app)
**How it works**: Clicking a document name opens a document viewer/preview, NOT a direct download. Must find and click a "Download" button inside the viewer.
**Playwright**: Use `browser_run_code` to locate the Download button (may be inside a frame):
```js
async (page) => {
    const frame = page.frames().find(f => f.url().includes('blob') || f.url().includes('pdf') || f.url() !== page.url());
    if (frame) {
        const dl = frame.getByRole('button', { name: 'Download' });
        if (await dl.count() > 0) { await dl.click(); return 'Clicked download in frame'; }
    }
    const dl = page.getByRole('button', { name: 'Download' });
    if (await dl.count() > 0) { await dl.first().click(); return 'Clicked download on page'; }
    return 'No download button found';
}
```
**Pitfall**: Downloaded file is often named generically (e.g., `document.pdf`) — must rename based on registry.

### 3. Export to Excel
**Portals**: BCGK InvestorCafe (Transactions page)
**How it works**: Table data with "Export to Excel" button that downloads an .xlsx file.
**Playwright**: Click the Export button — Playwright captures the download.
**Pitfall**: Export includes ALL data (all years). May need to filter or document that.

### 4. Fetch-Based Download (Inline PDF / S3 Signed URL)
**Portals**: Ziprent, Just Appraised (S3 signed URLs)
**How it works**: Download URL returns a PDF with `Content-Disposition: inline` or opens in browser's PDF viewer. Playwright captures the viewer HTML, not the actual PDF. Just Appraised serves PDFs from `ja-file-uploads.s3.amazonaws.com` with time-limited signed URLs.
**Playwright**: Use `page.evaluate(fetch())` to get raw PDF bytes, then create a data: URL download link:
```js
async (page) => {
    const b64 = await page.evaluate(async () => {
        const r = await fetch('/path/to/download', { credentials: 'include' });
        const blob = await r.blob();
        const ab = await blob.arrayBuffer();
        const bytes = new Uint8Array(ab);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        return btoa(binary);
    });
    await page.evaluate((data) => {
        const link = document.createElement('a');
        link.href = 'data:application/pdf;base64,' + data;
        link.download = 'filename.pdf';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }, b64);
    return 'Download triggered';
}
```

## MFA Strategies

### Mobile App Push (Chase)
- Send Slack message asking user to approve
- Wait for page to auto-advance after approval
- If timeout: click "Get another notification" link, re-notify user

### Email PIN (Obie Insurance)
- Login IS the MFA — email + 6-digit PIN every time
- PIN input uses SEPARATE input fields per digit
- Use `browser_run_code` to fill each field:
```js
async (page) => {
    const pin = 'XXXXXX';
    const inputs = await page.locator('[data-testid="keypad-input-element"]').all();
    for (let i = 0; i < pin.length && i < inputs.length; i++) {
        await inputs[i].fill(pin[i]);
    }
    return `Entered ${pin.length} digits`;
}
```

### 7-Digit 2FA (InvestorCafe/Yardi)
- Code sent to email automatically after password
- 7 individual digit input fields
- Timer expires — resend via Email/Text/Voice buttons
- Fill each digit field separately via browser_run_code

### SMS MFA (E-Trade, Homebase)
- Code sent to registered phone
- Single input field for the code
- Standard fill + submit

## Cloudflare Bypass
**Portals**: San Mateo county-taxes.net
- Look for Turnstile iframe (URL contains 'challenges.cloudflare.com')
- Click checkbox or body inside the iframe using `browser_run_code`
- Wait for page to reload after verification

## Browser Selection

**cursor-ide-browser** (Cursor's built-in Electron Chromium):
- Works for most portals
- Faster to launch (already running)
- FAILS on Auth0 redirects (Just Appraised — returns "sent an invalid response")
- FAILS when site requires specific Chrome/Chromium version or TLS features

**user-playwright** (standalone Playwright Chrome):
- Works for ALL portals tested including Auth0
- Requires MCP to be enabled in Cursor Settings
- Browser profile at `browser-profile/` — may need lock file cleanup if Chrome crashes
- Recovery: kill Chrome processes using `browser-profile`, remove `SingletonLock`/`SingletonSocket`/`SingletonCookie`, retry
- If MCP server itself is dead: toggle disable/enable in Cursor Settings > MCP (requires user action)

**Rule**: Try cursor-ide-browser first. If auth/redirect fails, switch to user-playwright.

## General Patterns
1. **Tax forms are often hidden** — not in main nav. Check: account dropdown menu, profile/settings, separate "Tax" or "Documents" section
2. **Multiple accounts/properties** — some portals show all, some require switching. Check for account selectors
3. **Filename standardization** — portals use inconsistent names. Always rename to: `{year} {Form Type} - {Issuer} - {Description}.{ext}`
4. **Credential source priority**: macOS Keychain > Chrome Passwords CSV > ask user via Slack
5. **Session persistence**: Playwright browser profile persists cookies. Re-login only if session expired
