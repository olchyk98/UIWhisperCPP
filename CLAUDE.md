# UIWhisperCPP

## Instructions for agents

- Keep `stories.md` (next to this file) current: whenever a change alters what the app does for the user, update its scenarios in the same turn. It is a living spec of the app as it is now: add, edit or delete scenarios in the existing shape; no ticket ids, dates or "now"/"previously". Commit it together with the code it describes.
- Run from source: `uv run app`. Build the .app: `./build.sh` (PyInstaller, `UIWhisperCPP.spec`).
- Check a build without opening the UI: `dist/UIWhisperCPP.app/Contents/MacOS/UIWhisperCPP --self-test`.
- App version and minimum macOS live in `src/uiwhispercpp/version.py`; bump the version before
  publishing a release, and attach `UIWhisperCPP.app.zip` to the GitHub release (the updater
  downloads that asset and compares the tag, e.g. `v1.6`, with the version).
- Updater testing hooks: `UIWHISPERCPP_FAKE_VERSION=1.0` pretends to be an older version;
  `UIWHISPERCPP_GITHUB_API=<url>` points the release lookup at another API base.
- Testing without touching real preferences: `UIWHISPERCPP_SETTINGS_FILE=/tmp/x.ini uv run app`
  stores all settings in that INI file instead of the user's defaults.
- Debug log: `~/Library/Logs/UIWhisperCPP/app.log` (also Help > Debug Log in the app). It captures
  Python logging plus raw stdout/stderr of the process and its children; check it first when
  something misbehaves.
- Errors shown to users must be `UserFacingError` (models/base.py) with plain wording; anything
  else is shown as a generic message and logged with its traceback.
