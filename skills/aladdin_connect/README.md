# Aladdin Connect skill

Genie Aladdin Connect via Cognito `USER_PASSWORD_AUTH` + `api.smartgarage.systems`.

## Env

| Env | Purpose |
|---|---|
| `ALADDIN_USERNAME` / `ALADDIN_PASSWORD` | Account that **owns** the door |
| `ALADDIN_DRY_RUN` | `1` (default) logs open; `0` sends OPEN_DOOR |
| `ALADDIN_DEVICE_SERIAL` | Pin e.g. `F0AD4E3E7403` (Big Peach) |
| `ALADDIN_DOOR_INDEX` | Door index on that device (Big Peach = `1`) |
| `ALADDIN_DOOR_NAME` | Fallback match (`Big Peach`) |

Shared/guest doors are not opened — ownership is required.

Cognito app client id/secret are Genie's public mobile-app credentials (same
ones Home Assistant uses), not operator secrets.
