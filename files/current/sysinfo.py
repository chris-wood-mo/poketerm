#!/usr/bin/env python3
"""Turn fastfetch's JSON output into the system-details lines poketerm shows.

poketerm used to get these lines from neofetch, which is no longer maintained
and is missing from newer Linux distributions. fastfetch replaces it, but its
own output looks different, so this script rebuilds each line the way
neofetch's config in poketerm printed it: same labels, same colours, same
wording for every value. A terminal looks the same whichever tool is used.

Usage: fastfetch --format json -s OS:Host:Kernel:Uptime:Packages:Shell:Terminal \
           | python3 sysinfo.py
"""
import json
import os
import re
import shutil
import subprocess
import sys

RESET = "\033[0m"
BOLD = "\033[1m"

# The label colour neofetch picks for each OS, from the colours of its logo
# (see set_text_colors in neofetch 7.1). Keyed on os-release ID. An OS
# neofetch had no logo for falls back to its Tux logo: bold, no colour.
LABEL_COLOURS = {
    "macos": 3,
    "ubuntu": 1, "debian": 1, "raspbian": 1, "rhel": 1, "ol": 1,
    "linuxmint": 2, "manjaro": 2, "opensuse": 2, "opensuse-tumbleweed": 2,
    "opensuse-leap": 2, "opensuse-microos": 2, "sles": 2, "centos": 2,
    "deepin": 2, "neon": 2, "void": 8,
    "amzn": 3,
    "fedora": 4, "elementary": 4, "alpine": 5, "solus": 4, "slackware": 4,
    "kali": 8,
    "gentoo": 5, "endeavouros": 5,
    "arch": 6, "archarm": 6, "artix": 6, "pop": 6, "zorin": 6, "nixos": 6,
    "mx": 6, "parrot": 6,
    "garuda": 7,
}

# neofetch strips these placeholder strings from the Host value.
DUMMY_HOST = [
    "To be filled by O.E.M.", "Not Applicable", "System Product Name",
    "System Version", "Undefined", "Default string", "Not Specified",
    "Type1ProductConfigId", "INVALID", "All Series", "�",
]

# neofetch's package manager labels, in the order it lists them. Each entry
# is (label, fastfetch JSON keys summed for it).
LINUX_PACKAGES = [
    ("kiss", ["kiss"]), ("pacman", ["pacman"]), ("dpkg", ["dpkg"]),
    ("rpm", ["rpm"]), ("xbps-query", ["xbps"]), ("apk", ["apk"]),
    ("opkg", ["opkg"]), ("brew", ["brew"]), ("emerge", ["emerge"]),
    ("eopkg", ["eopkg"]), ("pkgtool", ["pkgtool"]), ("sorcery", ["sorcery"]),
    ("cave", ["paludis"]), ("guix-system", ["guixSystem"]),
    ("guix-user", ["guixUser"]), ("nix-system", ["nixSystem"]),
    ("nix-user", ["nixUser"]), ("nix-default", ["nixDefault"]),
    ("pkg", ["pkg"]), ("flatpak", ["flatpakSystem", "flatpakUser"]),
    ("snap", ["snap"]),
]
MAC_PACKAGES = [
    ("port", ["macports"]), ("brew", ["brew"]), ("pkgin", ["pkgin"]),
    ("nix-system", ["nixSystem"]), ("nix-user", ["nixUser"]),
]

MAC_CODENAMES = [
    ("10.4", "Mac OS X Tiger"), ("10.5", "Mac OS X Leopard"),
    ("10.6", "Mac OS X Snow Leopard"), ("10.7", "Mac OS X Lion"),
    ("10.8", "OS X Mountain Lion"), ("10.9", "OS X Mavericks"),
    ("10.10", "OS X Yosemite"), ("10.11", "OS X El Capitan"),
    ("10.12", "macOS Sierra"), ("10.13", "macOS High Sierra"),
    ("10.14", "macOS Mojave"), ("10.15", "macOS Catalina"),
    ("10.16", "macOS Big Sur"), ("11.0", "macOS Big Sur"),
]


def colour(n):
    """neofetch's color() function."""
    if 0 <= n <= 6:
        return RESET + "\033[3%dm" % n
    if n == 7:
        return "\033[37m" + RESET
    return "\033[38;5;%dm" % n


def label_colour(os_id, is_mac):
    if is_mac:
        n = LABEL_COLOURS["macos"]
    else:
        n = LABEL_COLOURS.get(os_id)
        if n is None:
            return RESET  # Tux logo: bold label, default colour
    if n == 8:
        return RESET
    return colour(n)


def release_file_values(path):
    values = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("\"'")
    except OSError:
        return None
    return values


def os_release_value(key):
    for path in ("/usr/lib/os-release", "/etc/os-release"):
        values = release_file_values(path)
        if values is not None:
            return values.get(key, "")
    return ""


def linux_distro():
    """The OS name exactly as neofetch found it (fastfetch's own prettyName
    adds detail, e.g. Debian's point release)."""
    if shutil.which("lsb_release"):
        try:
            out = subprocess.run(["lsb_release", "-sd"], capture_output=True, text=True,
                                 timeout=2, stdin=subprocess.DEVNULL).stdout
            out = out.strip().strip("\"'")
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    # Like neofetch: use the first of these files that exists.
    for path in ("/etc/lsb-release", "/usr/lib/os-release", "/etc/os-release", "/etc/openwrt_release"):
        values = release_file_values(path)
        if values is not None:
            name = values.get("PRETTY_NAME") or values.get("DISTRIB_DESCRIPTION", "")
            codename = values.get("UBUNTU_CODENAME", "")
            return (name + " " + codename).strip()
    return ""


def os_line(data, is_mac, arch):
    r = data.get("OS") or {}
    if is_mac:
        version = r.get("version", "")
        name = "macOS"
        for prefix, codename in MAC_CODENAMES:
            if version == prefix or version.startswith(prefix + "."):
                name = codename
        distro = " ".join(p for p in (name, version, r.get("buildID", "")) if p)
    else:
        distro = linux_distro() or r.get("prettyName") or ""
        try:
            with open("/proc/version", encoding="utf-8", errors="replace") as f:
                if "Microsoft" in f.read():
                    distro += " on Windows 10"
        except OSError:
            pass
        distro = distro.strip()
        if distro.startswith("Ubuntu"):
            flavours = [("plasma", "Kubuntu"), ("mate", "Ubuntu MATE"),
                        ("xubuntu", "Xubuntu"), ("Lubuntu", "Lubuntu"),
                        ("budgie", "Ubuntu Budgie"), ("studio", "Ubuntu Studio"),
                        ("cinnamon", "Ubuntu Cinnamon")]
            dirs = os.environ.get("XDG_CONFIG_DIRS", "")
            for needle, flavour in flavours:
                if needle in dirs:
                    distro = distro.replace("Ubuntu", flavour, 1)
                    break
    distro = distro.replace("Enterprise Server", "")
    if not distro:
        distro = ("macOS" if is_mac else "Linux") + " (Unknown)"
    return (distro + " " + arch).strip() if arch else distro


def host_line(data, is_mac):
    r = data.get("Host") or {}
    if is_mac:
        model = r.get("family") or ""
    else:
        model = " ".join([r.get("name") or "", r.get("version") or ""])
    for junk in DUMMY_HOST:
        model = model.replace(junk, "")
    model = re.sub(r"To Be Filled.*", "", model)
    model = re.sub(r"OEM.*", "", model)
    if model.startswith("Standard PC"):
        model = "KVM/QEMU (%s)" % model
    return model


def uptime_line(data):
    ms = (data.get("Uptime") or {}).get("uptime")
    if not isinstance(ms, (int, float)):
        return ""
    s = int(ms // 1000)
    parts = []
    for amount, unit in ((s // 86400, "day"), (s // 3600 % 24, "hour"), (s // 60 % 60, "min")):
        if amount:
            parts.append("%d %s%s" % (amount, unit, "" if amount == 1 else "s"))
    return ", ".join(parts) or "%d secs" % s


def packages_line(data, is_mac):
    r = data.get("Packages") or {}
    out = []
    for label, keys in (MAC_PACKAGES if is_mac else LINUX_PACKAGES):
        n = sum(r.get(k, 0) or 0 for k in keys if isinstance(r.get(k, 0), int))
        if n > 0:
            out.append("%d (%s)" % (n, label))
    return ", ".join(out)


def shell_line(data):
    r = data.get("Shell") or {}
    shell_env = os.environ.get("SHELL", "")
    name = shell_env.rsplit("/", 1)[-1] if shell_env else (r.get("exeName") or r.get("processName") or "")
    if not name:
        return ""
    version = ""
    if r.get("exeName") == name or r.get("processName") == name:
        version = r.get("version") or ""
    if not version and shell_env:
        # Not the shell running poketerm, or fastfetch couldn't tell: ask it.
        version = other_shell_version(shell_env, name)
    if name == "bash":
        version = version.split("-", 1)[0]
    version = re.sub(r"\(.*\)", "", version.replace(", version", "")).strip()
    return (name + " " + version).strip()


def other_shell_version(path, name):
    """Version of a login shell that isn't the one running poketerm."""
    if name in ("sh", "ash", "dash", "es"):
        return ""
    var = {"bash": "BASH_VERSION", "tcsh": "tcsh"}.get(name)
    try:
        if var:
            cmd = [path, "-c", 'printf %%s "$%s"' % var]
        elif name.endswith("ksh"):
            cmd = [path, "-c", 'printf %s "$KSH_VERSION"']
        else:
            cmd = [path, "--version"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1,
                             stdin=subprocess.DEVNULL).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not out:
        return ""
    version = out[0].replace(name + " ", "", 1) if not var else out[0]
    return version.replace(" KSH", "").replace("version", "").strip()


def terminal_line(data):
    env = os.environ
    term = {
        "iTerm.app": "iTerm2", "Terminal.app": "Apple Terminal",
        "Hyper": "HyperTerm",
    }.get(env.get("TERM_PROGRAM", ""), env.get("TERM_PROGRAM", "").replace(".app", "", 1))
    if env.get("TERM") in ("tw52", "tw100"):
        term = "TosWin2"
    if env.get("SSH_CONNECTION"):
        term = env.get("SSH_TTY", "")
    if env.get("WT_SESSION"):
        term = "Windows Terminal"
    if term:
        return term

    r = data.get("Terminal") or {}
    name = (r.get("processName") or r.get("exeName") or "").replace(" ", "")
    if not name or re.match(r"(ruby|1|tmux.*|systemd|sshd.*|python.*|kdeinit.*|launchd.*)$", name):
        return ""
    if re.match(r"(login.*|.*Login.*|init|\(init\))$", name):
        return r.get("tty") or ""
    if name.startswith("gnome-terminal"):
        return "gnome-terminal"
    if name == "urxvtd":
        return "urxvt"
    if name.endswith("nvim"):
        return "Neovim Terminal"
    if "NeoVimServer" in name:
        return "VimR Terminal"
    name = name.rsplit("/", 1)[-1]
    if name.startswith(".") and name.endswith("-wrapped"):
        name = name[1:-len("-wrapped")]
    return name


def main():
    try:
        modules = json.load(sys.stdin)
    except ValueError:
        return
    data = {}
    for m in modules if isinstance(modules, list) else []:
        if isinstance(m, dict) and isinstance(m.get("result"), dict):
            data[m.get("type")] = m["result"]

    kernel = data.get("Kernel") or {}
    is_mac = kernel.get("name") == "Darwin" or (data.get("OS") or {}).get("id") == "macos"
    arch = os.uname().machine or kernel.get("architecture") or ""  # neofetch used uname -m
    os_id = (data.get("OS") or {}).get("id") or os_release_value("ID")

    lines = [
        ("OS", lambda: os_line(data, is_mac, arch)),
        ("Host", lambda: host_line(data, is_mac)),
        ("Kernel", lambda: kernel.get("release") or ""),
        ("Uptime", lambda: uptime_line(data)),
        ("Packages", lambda: packages_line(data, is_mac)),
        ("Shell", lambda: shell_line(data)),
        ("Terminal", lambda: terminal_line(data)),
    ]
    sub = label_colour(os_id, is_mac)
    out = []
    for label, get in lines:
        try:
            value = get()
        except Exception:  # one odd value must never hide the others
            value = ""
        value = " ".join(value.replace(RESET, "").split())
        if value:
            # Byte-for-byte what neofetch's prin() printed.
            out.append("%s%s%s%s%s:%s %s%s " % (sub, BOLD, label, RESET, RESET, RESET, value, RESET))
    sys.stdout.write("\n".join(out) + ("\n" if out else ""))


if __name__ == "__main__":
    main()
