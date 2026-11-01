"""Self-update from GitHub releases.

Flow: `check_for_update` asks the GitHub API for the repo's latest release and
compares its tag (e.g. "v1.6") with this app's version. `download_update`
streams the release's `.app.zip` asset with progress and unpacks it with
`ditto` (which, unlike zipfile, keeps the bundle's symlinks and permissions).
`install_and_relaunch` hands off to a tiny shell script that waits for this
process to exit, swaps the new bundle into place (keeping a backup until the
swap succeeds) and opens it.

Only the packaged .app can update itself; a source checkout is updated with git.

Testing hooks (environment variables):
  UIWHISPERCPP_FAKE_VERSION  pretend to be this version (e.g. "1.0")
  UIWHISPERCPP_GITHUB_API    API base URL instead of https://api.github.com
"""
from collections.abc import Callable
from dataclasses import dataclass
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from uiwhispercpp.version import __version__

_ASSET_SUFFIX = ".app.zip"
_CHUNK = 256 * 1024
_TIMEOUT = 20


class UpdateError(Exception):
  """A user-presentable update failure."""


@dataclass(frozen=True)
class Release:
  version: str
  tag: str
  name: str
  asset_url: str
  asset_size: int


def current_version() -> str:
  return os.environ.get("UIWHISPERCPP_FAKE_VERSION") or __version__


def _api_base() -> str:
  return os.environ.get("UIWHISPERCPP_GITHUB_API", "https://api.github.com").rstrip("/")


def parse_repo(text: str) -> str:
  """Normalize "owner/repo", a github.com URL or a git remote to "owner/repo"."""
  cleaned = text.strip().removesuffix("/").removesuffix(".git")
  match = re.search(r"github\.com[/:]([^/\s]+)/([^/\s]+)$", cleaned)
  if match:
    return f"{match.group(1)}/{match.group(2)}"
  if re.fullmatch(r"[\w.-]+/[\w.-]+", cleaned):
    return cleaned
  raise UpdateError(
    f"'{text}' isn't a GitHub repository. Use the form owner/repo, "
    "e.g. olchyk98/UIWhisperCPP."
  )


def version_tuple(text: str) -> tuple[int, ...]:
  numbers = re.findall(r"\d+", text)
  return tuple(int(n) for n in numbers) if numbers else (0,)


def is_newer(candidate: str, current: str) -> bool:
  a, b = version_tuple(candidate), version_tuple(current)
  width = max(len(a), len(b))
  return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _ssl_context() -> ssl.SSLContext:
  # The bundled Python may not see the system keychain; certifi ships a CA set.
  try:
    import certifi
    return ssl.create_default_context(cafile=certifi.where())
  except ImportError:
    return ssl.create_default_context()


def _open(url: str, accept: str) -> object:
  request = urllib.request.Request(
    url, headers={"Accept": accept, "User-Agent": f"UIWhisperCPP/{__version__}"}
  )
  try:
    return urllib.request.urlopen(request, timeout=_TIMEOUT, context=_ssl_context())
  except urllib.error.HTTPError as error:
    if error.code == 404:
      raise UpdateError("No releases found for that repository.") from error
    if error.code == 403:
      raise UpdateError("GitHub is rate-limiting requests. Try again later.") from error
    raise UpdateError(f"GitHub returned an error ({error.code}).") from error
  except (urllib.error.URLError, TimeoutError, OSError) as error:
    raise UpdateError("Couldn't reach GitHub. Check your internet connection.") from error


def fetch_latest_release(repo: str) -> Release:
  owner_repo = parse_repo(repo)
  with _open(f"{_api_base()}/repos/{owner_repo}/releases/latest",
             "application/vnd.github+json") as response:
    data = json.load(response)
  assets = [a for a in data.get("assets", []) if a.get("name", "").endswith(_ASSET_SUFFIX)]
  if not assets:
    raise UpdateError(
      f"The latest release ({data.get('tag_name', '?')}) has no app download attached."
    )
  asset = next((a for a in assets if a["name"] == "UIWhisperCPP.app.zip"), assets[0])
  tag = data.get("tag_name", "")
  return Release(
    version=".".join(str(n) for n in version_tuple(tag)),
    tag=tag,
    name=data.get("name") or tag,
    asset_url=asset["browser_download_url"],
    asset_size=int(asset.get("size") or 0),
  )


def check_for_update(repo: str) -> Release | None:
  """The latest release when it is newer than this app, else None."""
  release = fetch_latest_release(repo)
  return release if is_newer(release.version, current_version()) else None


def running_app_bundle() -> str | None:
  """Path of the running .app bundle, or None when not running from one."""
  if not getattr(sys, "frozen", False):
    return None
  path = os.path.realpath(sys.executable)
  marker = ".app/Contents/MacOS/"
  index = path.find(marker)
  return path[: index + len(".app")] if index != -1 else None


def can_self_update() -> tuple[bool, str]:
  """Whether this copy can replace itself, with a reason when it can't."""
  bundle = running_app_bundle()
  if bundle is None:
    return False, (
      "You're running UIWhisperCPP from source. Update it with git pull instead."
    )
  if not os.access(os.path.dirname(bundle), os.W_OK):
    return False, (
      f"No permission to replace the app in {os.path.dirname(bundle)}. "
      "Move it to Applications or your user folder and try again."
    )
  return True, ""


def download_update(
  release: Release,
  on_progress: Callable[[int, int], None],
  is_cancelled: Callable[[], bool] = lambda: False,
) -> str:
  """Download and unpack the release; return the path of the new .app bundle."""
  work_dir = tempfile.mkdtemp(prefix="uiwhispercpp-update-")
  zip_path = os.path.join(work_dir, "update.zip")
  with _open(release.asset_url, "application/octet-stream") as response:
    total = int(response.headers.get("Content-Length") or release.asset_size or 0)
    done = 0
    with open(zip_path, "wb") as out:
      while chunk := response.read(_CHUNK):
        if is_cancelled():
          shutil.rmtree(work_dir, ignore_errors=True)
          raise UpdateError("Update cancelled.")
        out.write(chunk)
        done += len(chunk)
        on_progress(done, total)
  extract_dir = os.path.join(work_dir, "extracted")
  result = subprocess.run(
    ["ditto", "-x", "-k", zip_path, extract_dir], capture_output=True, text=True
  )
  if result.returncode != 0:
    raise UpdateError("The downloaded update is damaged. Try again.")
  os.remove(zip_path)
  bundles = [n for n in os.listdir(extract_dir) if n.endswith(".app")]
  if not bundles:
    raise UpdateError("The downloaded update doesn't contain the app.")
  new_bundle = os.path.join(extract_dir, bundles[0])
  if not os.path.isfile(os.path.join(new_bundle, "Contents", "Info.plist")):
    raise UpdateError("The downloaded update doesn't contain a valid app.")
  return new_bundle


_INSTALL_SCRIPT = r"""#!/bin/sh
# Wait for the running app to quit, swap in the new bundle, relaunch it.
PID="$1"; NEW="$2"; TARGET="$3"; BACKUP="$TARGET.update-backup"
while kill -0 "$PID" 2>/dev/null; do sleep 0.2; done
rm -rf "$BACKUP"
if mv "$TARGET" "$BACKUP" && mv "$NEW" "$TARGET"; then
  xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null
  rm -rf "$BACKUP"
else
  [ -d "$BACKUP" ] && [ ! -d "$TARGET" ] && mv "$BACKUP" "$TARGET"
fi
open "$TARGET"
# NEW is <work dir>/extracted/<name>.app; drop the whole work dir.
rm -rf "$(dirname "$(dirname "$NEW")")" "$0"
"""


def install_and_relaunch(new_bundle: str, target_bundle: str) -> None:
  """Start the swap helper; the caller must quit the app right after."""
  fd, script_path = tempfile.mkstemp(prefix="uiwhispercpp-install-", suffix=".sh")
  with os.fdopen(fd, "w") as script:
    script.write(_INSTALL_SCRIPT)
  os.chmod(script_path, 0o755)
  subprocess.Popen(
    ["/bin/sh", script_path, str(os.getpid()), new_bundle, target_bundle],
    start_new_session=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
  )
