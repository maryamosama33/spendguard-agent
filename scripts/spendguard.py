"""One entry point to set up and run SpendGuard on Hermes.

    python scripts/spendguard.py setup           install the "spendguard" Hermes profile
    python scripts/spendguard.py chat            chat in the terminal (judge demo, no Telegram)
    python scripts/spendguard.py chat "<msg>"    send one message and print the reply
    python scripts/spendguard.py bot             run the Telegram bot (keep the window open)
    python scripts/spendguard.py reset           reset demo data (seeded price history only)

Run it with the repo's venv Python: Hermes starts the SpendGuard MCP server
with this same interpreter.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE = "spendguard"
PROFILE_SRC = REPO_ROOT / "hermes" / "profile"
PLUGIN_SRC = REPO_ROOT / "hermes" / "plugins" / "spendguard-hermes"
SOUL_SRC = REPO_ROOT / "hermes" / "SOUL.md"
# Copied from the repo .env into the profile's .env (values never printed).
HERMES_KEYS = ["GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS",
               "TELEGRAM_HOME_CHANNEL", "SPENDGUARD_OWNER_IDS"]

GATEWAY_ADMINS_TEMPLATE = """# Owners (SPENDGUARD_OWNER_IDS) get every slash command; other allowed users
# only /help and /whoami (F26). Approving/rejecting is owner-only too (plugin).
gateway:
  platforms:
    telegram:
      extra:
        allow_admin_from: {ids}
        group_allow_admin_from: {ids}
"""


def _hermes(*args: str, cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess:
    hermes = shutil.which("hermes")
    if hermes is None:
        sys.exit("Hermes Agent is not installed or not on PATH (see README).")
    return subprocess.run([hermes, *args], cwd=cwd, text=True, encoding="utf-8",
                          errors="replace", capture_output=capture)


def _owner_ids() -> list[str]:
    raw = _read_env(REPO_ROOT / ".env").get("SPENDGUARD_OWNER_IDS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _gateway_admins_block() -> str:
    """Hermes admin list for Telegram; omitted when no owners are set (no gating)."""
    ids = _owner_ids()
    return GATEWAY_ADMINS_TEMPLATE.format(ids=json.dumps(ids)) if ids else ""


def _render_config() -> str:
    template = (PROFILE_SRC / "config.template.yaml").read_text(encoding="utf-8")
    return (template.replace("{{PYTHON}}", Path(sys.executable).as_posix())
                    .replace("{{REPO_ROOT}}", REPO_ROOT.as_posix())
                    .replace("{{GATEWAY_ADMINS}}", _gateway_admins_block()))


def _stage_profile(stage: Path) -> None:
    """A Hermes profile distribution: manifest, SOUL, config, guardrails plugin."""
    shutil.copy(PROFILE_SRC / "distribution.yaml", stage / "distribution.yaml")
    shutil.copy(SOUL_SRC, stage / "SOUL.md")
    (stage / "config.yaml").write_text(_render_config(), encoding="utf-8")
    shutil.copytree(PLUGIN_SRC, stage / "plugins" / "spendguard-hermes",
                    ignore=shutil.ignore_patterns("__pycache__"))


def _profile_dir() -> Path:
    out = _hermes("-p", PROFILE, "config", "path", capture=True).stdout.strip().splitlines()
    return Path(out[-1]).parent


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
             if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip('"') for k, v in pairs}


def _copy_keys(profile_env: Path) -> list[str]:
    """Merge the Hermes keys from the repo .env into the profile .env; return their names."""
    repo_env = _read_env(REPO_ROOT / ".env")
    keys = {k: repo_env[k] for k in HERMES_KEYS if repo_env.get(k)}
    existing = [l for l in (profile_env.read_text(encoding="utf-8").splitlines() if profile_env.exists() else [])
                if l.split("=", 1)[0].strip() not in keys]
    profile_env.write_text("\n".join(existing + [f"{k}={v}" for k, v in keys.items()]) + "\n",
                           encoding="utf-8")
    return list(keys)


def _workspace() -> Path:
    """Hermes runs from here, outside the repo, so it doesn't switch to coding mode."""
    path = _profile_dir() / "workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup() -> None:
    subprocess.run([sys.executable, "-m", "spendguard.seed"], cwd=REPO_ROOT, check=True)
    with tempfile.TemporaryDirectory() as stage:
        _stage_profile(Path(stage))
        if _hermes("profile", "install", stage, "--force", "-y").returncode != 0:
            sys.exit("hermes profile install failed (see output above).")
    copied = _copy_keys(_profile_dir() / ".env")
    _hermes("-p", PROFILE, "plugins", "enable", "spendguard-hermes")
    missing = [k for k in ("GROQ_API_KEY",) if k not in copied]
    print(f"\nSpendGuard profile installed. Keys copied: {', '.join(copied) or 'none'}")
    if missing:
        print(f"Missing in .env: {', '.join(missing)} (required).")
    owners = _owner_ids()
    print(f"Owners (approve/reject + all /commands): {', '.join(owners)}" if owners else
          "No SPENDGUARD_OWNER_IDS set: any allowed user can approve and use /commands.")
    print("Next: python scripts/spendguard.py chat   (or: bot, for Telegram)")


def chat(message: str | None) -> None:
    args = ["-p", PROFILE, "chat", "--ignore-rules"]
    if message:
        args += ["-q", message, "--oneshot"]
    _hermes(*args, cwd=_workspace())


def bot() -> None:
    print("SpendGuard Telegram bot running. Keep this window open; Ctrl+C to stop.")
    _hermes("-p", PROFILE, "gateway", "run", cwd=_workspace())


def reset() -> None:
    subprocess.run([sys.executable, "-m", "spendguard.seed", "--force"], cwd=REPO_ROOT, check=True)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "setup":
        setup()
    elif command == "chat":
        chat(sys.argv[2] if len(sys.argv) > 2 else None)
    elif command == "bot":
        bot()
    elif command == "reset":
        reset()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
