# Portal Learnings

This directory stores navigation patterns learned during collaborative browser sessions.

Each portal has a JSON file (e.g., `schwab.json`) containing observations from sessions where the user helped the AI navigate. These learnings improve future automation runs.

## Format

```json
{
  "portal": "schwab",
  "learnings": [
    {
      "date": "2026-03-28",
      "type": "navigation|credential_flow|quirk|selector",
      "description": "Human-readable description of what was learned",
      "details": {
        "url": "...",
        "selector": "...",
        "before_state": "...",
        "after_state": "..."
      }
    }
  ]
}
```

## Learning Types

- **navigation**: How to get from page A to page B
- **credential_flow**: Login form quirks (iframes, multi-step, redirects)
- **quirk**: Unexpected behavior (session timeouts, popups, captchas)
- **selector**: Element selectors that work (and ones that don't)

## Privacy

These files are gitignored — they may contain portal-specific URLs or page structures that are user-specific.
