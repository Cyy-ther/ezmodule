#!/usr/bin/env python3
"""List kernel modules and DKMS-capable drivers on Arch Linux."""

from __future__ import annotations

import argparse
import fnmatch
import os
import select
import shutil
import subprocess
import sys
import termios
import tty
from dataclasses import dataclass
from pathlib import Path

try:
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover - optional UI dependency
    box = None
    Console = None
    Group = None
    Live = None
    Panel = None
    Table = None
    Text = None

try:
    import questionary
except ImportError:  # pragma: no cover - optional UI dependency
    questionary = None


@dataclass(frozen=True)
class ModuleEntry:
    name: str
    source: str
    status: str


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def detect_kernel_release() -> str:
    return os.uname().release


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This action requires root. Re-run with sudo.")


def list_loaded_modules() -> set[str]:
    path = Path("/proc/modules")
    if not path.exists():
        return set()
    loaded = set()
    for line in path.read_text().splitlines():
        if line.strip():
            loaded.add(line.split()[0])
    return loaded


def list_available_modules(kernel_release: str) -> list[str]:
    modules_dir = Path("/usr/lib/modules") / kernel_release
    if not modules_dir.exists():
        return []
    result = run(["find", str(modules_dir), "-type", "f", "-name", "*.ko*", "-printf", "%f\n"])
    if result.returncode != 0:
        return []
    modules = []
    for line in result.stdout.splitlines():
        name = line
        for suffix in (".ko.xz", ".ko.zst", ".ko.gz", ".ko"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        modules.append(name)
    return sorted(set(modules))


def normalize_dkms_name(value: str) -> str:
    name = value.strip()
    if not name:
        return ""
    name = name.split(",", 1)[0].strip()
    if "/" in name:
        name = name.split("/", 1)[0].strip()
    return name


def list_dkms_modules() -> list[str]:
    if shutil.which("dkms") is None:
        return []
    result = run(["dkms", "status"])
    if result.returncode != 0:
        return []
    modules = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name = normalize_dkms_name(line)
        if name:
            modules.add(name)
    return sorted(modules)


def module_names_for_listing(kernel_release: str) -> tuple[list[str], set[str]]:
    available = list_available_modules(kernel_release)
    dkms = set(list_dkms_modules())
    return sorted(set(available) | dkms), dkms


def collect_entries(kernel_release: str, search: str | None) -> list[ModuleEntry]:
    names, dkms = module_names_for_listing(kernel_release)
    loaded = list_loaded_modules()
    entries: list[ModuleEntry] = []
    for name in names:
        if search:
            pattern = search
            if pattern not in name and not fnmatch.fnmatch(name, pattern):
                continue
        status = "loaded" if name in loaded else "available"
        source = "dkms" if name in dkms else "kernel"
        entries.append(ModuleEntry(name=name, source=source, status=status))
    return entries


def print_module_table(entries: list[ModuleEntry], kernel_release: str, search: str | None, dkms: set[str]) -> None:
    loaded = list_loaded_modules()
    print(f"Kernel: {kernel_release}")
    print(f"Loaded modules: {len(loaded)}")
    print(f"Available modules: {len(entries)}")
    print()

    if not entries:
        if search:
            print(f"No modules matched: {search}")
        else:
            print("No modules found for this kernel.")
        return

    for entry in entries:
        print(f"{entry.name:<40} {entry.status:<9} {entry.source}")

    if dkms:
        print()
        print("DKMS packages:")
        for name in sorted(dkms):
            print(f"  {name}")


def persist_modules(modules: list[str]) -> int:
    require_root()
    config_path = Path("/etc/modules-load.d/ezmodule.conf")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing = set()
    if config_path.exists():
        for line in config_path.read_text(encoding="utf-8").splitlines():
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                existing.add(cleaned.split()[0])

    with config_path.open("a", encoding="utf-8") as handle:
        for module in modules:
            if module in existing:
                continue
            if config_path.stat().st_size and not config_path.read_text(encoding="utf-8").endswith("\n"):
                handle.write("\n")
            handle.write(f"{module}\n")
            existing.add(module)

    print(f"Persisted modules to {config_path}")
    return 0


def blacklist_module(module: str) -> int:
    require_root()
    config_path = Path("/etc/modprobe.d/ezmodule-blacklist.conf")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if config_path.exists():
        for line in config_path.read_text(encoding="utf-8").splitlines():
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                existing.add(cleaned)

    if f"blacklist {module}" not in existing:
        with config_path.open("a", encoding="utf-8") as handle:
            if config_path.stat().st_size and not config_path.read_text(encoding="utf-8").endswith("\n"):
                handle.write("\n")
            handle.write(f"blacklist {module}\n")

    print(f"Blacklisted {module} in {config_path}")
    return 0


def normalize_module_name(path: str) -> str:
    name = Path(path).name
    for suffix in (".ko.xz", ".ko.zst", ".ko.gz", ".ko"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def read_module_dependencies_from_file(module: str) -> list[str]:
    dep_path = Path("/lib/modules") / detect_kernel_release() / "modules.dep"
    if not dep_path.exists():
        return []
    for line in dep_path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        left, right = line.split(":", 1)
        if normalize_module_name(left) != module:
            continue
        return [normalize_module_name(item) for item in right.split() if item.strip()]
    return []


def show_module_dependencies(module: str) -> int:
    result = run(["modprobe", "--show-depends", module])
    dependencies: list[str] = []

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or "insmod" not in stripped:
                continue
            path = stripped.split()[-1]
            dependency = normalize_module_name(path)
            if dependency:
                dependencies.append(dependency)

    if not dependencies:
        dependencies = read_module_dependencies_from_file(module)

    unique_dependencies = list(dict.fromkeys(dependencies))
    print(f"Dependency tree for {module}:")
    if not unique_dependencies:
        print("  (no dependency information available)")
        return 0

    root_dependencies = [name for name in unique_dependencies if name != module]
    print(f"{module}")
    for index, dependency in enumerate(root_dependencies):
        branch = "└─" if index == len(root_dependencies) - 1 else "├─"
        print(f"  {branch} {dependency}")
    return 0


def show_module_info(module: str) -> int:
    if shutil.which("modinfo") is None:
        sys.stderr.write("modinfo is not available on this system.\n")
        return 1

    result = run(["modinfo", module])
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout or f"Unable to inspect {module}\n")
        return result.returncode
    sys.stdout.write(result.stdout)
    return 0


def list_module_dependencies(module: str) -> list[str]:
    result = run(["modprobe", "--show-depends", module])
    dependencies: list[str] = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or "insmod" not in stripped:
                continue
            path = stripped.split()[-1]
            dep_name = normalize_module_name(path)
            if dep_name and dep_name != module:
                dependencies.append(dep_name)

    if not dependencies:
        dependencies = read_module_dependencies_from_file(module)

    unique: list[str] = []
    for dep in dependencies:
        if dep and dep not in unique:
            unique.append(dep)
    return unique


def list_module_parameters(module: str) -> int:
    params_dir = Path("/sys/module") / module / "parameters"
    if not params_dir.exists():
        sys.stderr.write(f"No parameter information is available for {module}.\n")
        return 1

    params = sorted(p.name for p in params_dir.iterdir() if p.is_file())
    if not params:
        print(f"{module} has no writable parameter entries under /sys/module/{module}/parameters/")
        return 0

    print(f"Parameters for {module}:")
    for name in params:
        param_path = params_dir / name
        try:
            value = param_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            value = "(unavailable)"
        print(f"  {name:<30} {value}")
    return 0


def set_module_parameter(module: str, assignment: str) -> int:
    if "=" not in assignment:
        sys.stderr.write("Parameter assignment must be in KEY=VALUE form.\n")
        return 2

    key, value = assignment.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        sys.stderr.write("Parameter name cannot be empty.\n")
        return 2

    params_dir = Path("/sys/module") / module / "parameters"
    param_path = params_dir / key
    if not param_path.exists():
        sys.stderr.write(f"Parameter {key} does not exist for module {module}.\n")
        return 1

    try:
        param_path.write_text(value, encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"Failed to set {module}:{key} = {value}: {exc}\n")
        return 1

    print(f"Set {module}:{key} = {value}")
    return 0


def show_modinfo_field(field: str, module: str) -> int:
    if shutil.which("modinfo") is None:
        sys.stderr.write("modinfo is not available on this system.\n")
        return 1
    result = run(["modinfo", "-F", field, module])
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout or f"Unable to read field {field!r} for {module}\n")
        return result.returncode
    value = result.stdout.strip()
    print(f"{field}: {value if value else '<empty>'}")
    return 0


def find_module_conflicts(module: str) -> list[str]:
    candidates: set[str] = set()
    conflict_map = {
        "nvidia": ["nouveau"],
        "nouveau": ["nvidia"],
        "ath9k": ["ath10k"],
        "ath10k": ["ath9k"],
        "i915": ["intel_agp"],
        "intel_agp": ["i915"],
        "r8169": ["r8168"],
        "r8168": ["r8169"],
        "mt7921e": ["mt7925e", "mt7915e"],
        "mt7925e": ["mt7921e", "mt7915e"],
        "mt7915e": ["mt7921e", "mt7925e"],
    }
    for key, options in conflict_map.items():
        if module == key:
            candidates.update(options)

    config_files = list(Path("/etc/modprobe.d").glob("*.conf")) + list(Path("/lib/modprobe.d").glob("*.conf"))
    for path in config_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if f"blacklist {module}" in stripped or f"install {module}" in stripped or f"alias {module}" in stripped:
                candidates.add(path.name)
            if module in stripped:
                candidates.add(stripped)

    loaded = list_loaded_modules()
    for other in sorted(loaded):
        if other == module:
            continue
        if any(other.startswith(prefix) for prefix in ["mt79", "i915", "ath", "nvidia", "nouveau", "r81"]):
            candidates.add(other)

    return sorted(candidates)


def check_conflicts(module: str) -> int:
    conflicts = find_module_conflicts(module)
    if not conflicts:
        print(f"No obvious conflicts detected for {module}.")
        return 0
    print(f"Potential conflicts for {module}:")
    for item in conflicts:
        print(f"  - {item}")
    return 0


def orphan_check() -> int:
    kernel = detect_kernel_release()
    stale_dirs: list[str] = []
    dkms_root = Path("/var/lib/dkms")
    if dkms_root.exists():
        for item in sorted(dkms_root.iterdir()):
            if item.is_dir() and not (Path("/lib/modules") / kernel / item.name).exists():
                stale_dirs.append(str(item / "build"))
    if not stale_dirs:
        print("No obvious orphaned DKMS/module artifacts found.")
        return 0

    print("Potential orphaned entries:")
    for item in stale_dirs:
        print(f"  - {item}")
    return 0


def _read_tui_key() -> str | None:
    if not select.select([sys.stdin], [], [], 0.05)[0]:
        return None

    ch = sys.stdin.buffer.read(1)
    if not ch:
        return None

    if ch == b"\x1b":
        extra = sys.stdin.buffer.read(2)
        if extra == b"[A":
            return "UP"
        if extra == b"[B":
            return "DOWN"
        if extra == b"[C":
            return "RIGHT"
        if extra == b"[D":
            return "LEFT"
        if extra == b"[H":
            return "HOME"
        if extra == b"[F":
            return "END"
        return "ESC"

    if ch in (b"\r", b"\n"):
        return "ENTER"
    if ch == b"\x7f":
        return "BACKSPACE"
    if ch == b"\x03":
        return "CTRL_C"
    return ch.decode(errors="ignore")


def _filter_module_entries(entries: list[ModuleEntry], query: str) -> list[ModuleEntry]:
    if not query:
        return entries
    needle = query.lower()
    return [entry for entry in entries if needle in entry.name.lower() or needle in entry.status.lower() or needle in entry.source.lower()]


def _render_module_list(entries: list[ModuleEntry], selected_index: int, query: str, kernel_release: str, action_index: int | None = None, action_mode: bool = False) -> Group:
    if Console is None or Table is None or Panel is None:
        raise RuntimeError("rich is required for the interactive TUI")

    search_line = Text(f"Search: {query or ' '}" if query else "Search: ", style="bold green")
    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Name", style="bold white", min_width=20, overflow="fold")
    table.add_column("Status", width=12, justify="center")
    table.add_column("Type", width=10, justify="center")

    terminal_height = shutil.get_terminal_size((80, 24)).lines
    page_size = max(8, min(len(entries), terminal_height - 10))
    start_index = max(0, min(selected_index, len(entries) - page_size))
    visible = entries[start_index : start_index + page_size]

    if not visible:
        table.add_row("No matches", "-", "-")
    else:
        for idx, entry in enumerate(visible):
            global_index = start_index + idx
            row_style = "on blue white" if global_index == selected_index else ""
            table.add_row(entry.name, entry.status, entry.source, style=row_style)

    list_panel = Panel(table, title=f"Kernel: {kernel_release}", border_style="cyan", padding=(0, 1))
    payload = [search_line, list_panel]

    if action_mode and entries:
        module = entries[selected_index].name
        actions = [
            "Enable/Load",
            "Disable/Unload",
            "View Info",
            "View Dependencies",
            "Toggle Persistence",
        ]
        action_table = Table.grid(expand=True)
        for pos, action in enumerate(actions):
            prefix = ">" if pos == action_index else " "
            action_table.add_row(Text(f"{prefix} {action}", style="bold white" if pos == action_index else "dim"))
        payload.append(Panel(action_table, title=f"Actions for {module}", border_style="magenta", padding=(0, 1)))

    payload.append(Text("↑/↓ move • Enter select • Type to filter • q quit", style="bold dim"))
    return Group(*payload)


def run_interactive_menu(kernel_release: str) -> int:
    if not sys.stdin.isatty():
        names, dkms = module_names_for_listing(kernel_release)
        del names
        entries = collect_entries(kernel_release, None)
        print_module_table(entries, kernel_release, None, dkms)
        return 0

    if Console is None or Live is None:
        if questionary is not None:
            names, dkms = module_names_for_listing(kernel_release)
            loaded = list_loaded_modules()
            choices = [
                questionary.Choice(title=f"{name} ({'loaded' if name in loaded else 'available'})", value=name)
                for name in names
            ]
            try:
                selected = questionary.checkbox("Select modules to toggle live", choices=choices).ask()
            except KeyboardInterrupt:
                print("\nCancelled.")
                return 130
            if not selected:
                return 0
            require_root()
            for module in selected:
                action = "disable" if module in loaded else "enable"
                result = apply_module_action(module, action)
                if result != 0:
                    return result
            return 0

        names, dkms = module_names_for_listing(kernel_release)
        del names
        entries = collect_entries(kernel_release, None)
        print_module_table(entries, kernel_release, None, dkms)
        return 0

    console = Console()
    all_entries = collect_entries(kernel_release, None)
    query = ""
    filtered = all_entries
    selected_index = 0
    action_index = 0
    action_mode = False
    status_message = "Navigate with ↑/↓, press Enter to select, q to quit."

    fd = sys.stdin.fileno()
    previous_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        with Live(console=console, auto_refresh=True, screen=False, refresh_per_second=20) as live:
            while True:
                filtered = _filter_module_entries(all_entries, query)
                if not filtered:
                    selected_index = 0
                elif selected_index >= len(filtered):
                    selected_index = len(filtered) - 1
                live.update(_render_module_list(filtered, selected_index, query, kernel_release, action_index if action_mode else None, action_mode))

                key = _read_tui_key()
                if key is None:
                    continue
                if key in {"CTRL_C", "q", "Q"}:
                    break
                if key == "UP":
                    if action_mode:
                        action_index = max(0, action_index - 1)
                    elif filtered:
                        selected_index = max(0, selected_index - 1)
                elif key == "DOWN":
                    if action_mode:
                        action_index = min(4, action_index + 1)
                    elif filtered:
                        selected_index = min(len(filtered) - 1, selected_index + 1)
                elif key == "ENTER":
                    if action_mode:
                        module_name = filtered[selected_index].name
                        actions = [
                            ("enable", apply_module_action),
                            ("disable", apply_module_action),
                            ("info", lambda name, _: show_module_info(name)),
                            ("deps", lambda name, _: show_module_dependencies(name)),
                            ("persist", lambda name, _: persist_modules([name])),
                        ]
                        action_name, handler = actions[action_index]
                        if action_name in {"enable", "disable"}:
                            result = handler(module_name, action_name)
                            status_message = f"{module_name} {action_name}d" if result == 0 else f"Action failed for {module_name}"
                        else:
                            result = handler(module_name, action_name)
                            status_message = f"{module_name} {action_name} action completed" if result == 0 else f"{module_name} {action_name} action failed"
                        action_mode = False
                        action_index = 0
                    elif filtered:
                        action_mode = True
                elif key == "ESC":
                    action_mode = False
                    action_index = 0
                elif key == "BACKSPACE":
                    query = query[:-1]
                    selected_index = 0
                elif key and key.isprintable() and key not in {"\x00"}:
                    query += key
                    selected_index = 0

                if status_message:
                    console.print(status_message, style="bold yellow")
                    status_message = ""

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous_settings)

    return 0


def ensure_dkms_module_ready(module: str) -> bool:
    if shutil.which("dkms") is None:
        return False

    if module not in set(list_dkms_modules()):
        return False

    kernel_release = detect_kernel_release()
    result = run(["dkms", "status"])
    package_version = None
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            normalized = normalize_dkms_name(line)
            if normalized == module:
                candidate = line.strip().split(",", 1)[0].strip()
                if "/" in candidate:
                    package_version = candidate.split("/", 1)[1].strip()
                    break

    if package_version:
        install = run(["dkms", "install", "-k", kernel_release, f"{module}/{package_version}"])
        if install.returncode == 0:
            return True

    autoinstall = run(["dkms", "autoinstall"])
    return autoinstall.returncode == 0


def resolve_dkms_spec(module: str) -> dict[str, str] | None:
    if shutil.which("dkms") is None:
        return None

    result = run(["dkms", "status"])
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        normalized = normalize_dkms_name(candidate)
        if normalized != module:
            continue

        first = candidate.split(",", 1)[0].strip() if "," in candidate else candidate.split(":", 1)[0].strip()
        if not first:
            continue
        if "/" in first:
            module_name, version = first.split("/", 1)
            return {"module": module_name.strip(), "version": version.strip()}
        return {"module": module.strip(), "version": ""}
    return None


def apply_dkms_action(module: str, action: str) -> int:
    require_root()
    if shutil.which("dkms") is None:
        sys.stderr.write("dkms is not installed on this system.\n")
        return 1

    dkms_spec = resolve_dkms_spec(module)
    if dkms_spec is None:
        sys.stderr.write(f"{module} is not listed as a DKMS package.\n")
        return 1

    kernel_release = detect_kernel_release()
    if action in {"enable", "install"}:
        install_cmd = ["dkms", "install", "-m", dkms_spec["module"], "-k", kernel_release]
        if dkms_spec["version"]:
            install_cmd.extend(["-v", dkms_spec["version"]])
        result = run(install_cmd)
        if result.returncode != 0:
            autoinstall = run(["dkms", "autoinstall"])
            if autoinstall.returncode != 0:
                log_path = Path("/var/lib/dkms") / dkms_spec["module"] / dkms_spec["version"] / "build" / "make.log"
                sys.stderr.write(f"\nDKMS build failed for {module} on kernel {kernel_release}.\n")
                sys.stderr.write(f"Log: {log_path}\n")
                if log_path.exists():
                    log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    tail = "\n".join(log_lines[-40:])
                    sys.stderr.write(f"\nRecent build output:\n{tail}\n")
                else:
                    sys.stderr.write("No build log was found. Ensure the DKMS package source and kernel headers are installed.\n")
                sys.stderr.write(result.stderr or result.stdout or "")
                return result.returncode
        modprobe_result = run(["modprobe", module])
        if modprobe_result.returncode != 0:
            sys.stderr.write(modprobe_result.stderr or modprobe_result.stdout or f"Failed to load DKMS module {module}\n")
            return modprobe_result.returncode
        print(f"Enabled DKMS module {module}")
        return 0

    if action in {"disable", "remove"}:
        unload = run(["modprobe", "-r", module])
        unload_text = (unload.stderr or unload.stdout or "").lower()
        if unload.returncode != 0 and not any(token in unload_text for token in ["not currently loaded", "not found", "not loaded", "module .* not found"]):
            sys.stderr.write(unload.stderr or unload.stdout or f"Failed to unload {module}\n")
            return unload.returncode
        remove_cmd = ["dkms", "remove", "-m", dkms_spec["module"], "-k", kernel_release]
        if dkms_spec["version"]:
            remove_cmd.extend(["-v", dkms_spec["version"]])
        result = run(remove_cmd)
        if result.returncode != 0:
            if any(token in (result.stderr or result.stdout or "").lower() for token in ["not found", "not installed", "no module", "not built"]):
                print(f"DKMS module {module} was already absent; cleanup skipped.")
                return 0
            sys.stderr.write(result.stderr or result.stdout or f"Failed to remove DKMS module {module}\n")
            return result.returncode
        print(f"Disabled DKMS module {module}")
        return 0

    raise SystemExit(f"Unknown DKMS action: {action}")


def apply_module_action(module: str, action: str) -> int:
    require_root()
    if action == "enable":
        dependencies = list_module_dependencies(module)
        for dependency in dependencies:
            dependency_result = run(["modprobe", dependency])
            if dependency_result.returncode != 0:
                sys.stderr.write(dependency_result.stderr or dependency_result.stdout or f"Failed to load dependency {dependency} for {module}\n")
                return dependency_result.returncode

        result = run(["modprobe", module])
        if result.returncode != 0:
            sys.stderr.write(result.stderr or result.stdout or f"Failed to load {module}\n")
            return result.returncode
        print(f"Enabled {module}")
        return 0

    if action == "disable":
        result = run(["modprobe", "-r", module])
        if result.returncode != 0:
            sys.stderr.write(result.stderr or result.stdout or f"Failed to unload {module}\n")
            return result.returncode
        print(f"Disabled {module}")
        return 0

    raise SystemExit(f"Unknown action: {action}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kernel module and DKMS management made simple.",
        epilog=(
            "Examples:\n"
            "  ezmodule --list\n"
            "  ezmodule -F nvidia\n"
            "  ezmodule --list-loaded\n"
            "  ezmodule --params usbcore\n"
            "  ezmodule --dkms-enable zenpower5\n"
            "  ezmodule --persist vfio\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    display_group = parser.add_argument_group("Display and filtering")
    display_group.add_argument("--kernel", default=detect_kernel_release(), help="Kernel release to inspect (default: running kernel).")
    display_group.add_argument("-S", "--search", "-F", dest="search", metavar="PATTERN", help="Filter modules by name with a shell pattern or substring.")
    display_group.add_argument("-L", "--list", action="store_true", help="Print the module list without launching the interactive menu.")
    display_group.add_argument("--list-loaded", action="store_true", help="Only show loaded kernel modules in list output.")

    module_group = parser.add_argument_group("Module actions")
    module_group.add_argument("--persist", metavar="MODULE", help="Persist a module in /etc/modules-load.d/ezmodule.conf.")
    module_group.add_argument("--blacklist", metavar="MODULE", help="Add a blacklist entry in /etc/modprobe.d/ezmodule-blacklist.conf.")
    module_group.add_argument("--deps", metavar="MODULE", help="Show the dependency tree for a module.")
    module_group.add_argument("--info", metavar="MODULE", help="Display module metadata using modinfo.")
    module_group.add_argument("--modinfo-field", nargs=2, metavar=("FIELD", "MODULE"), help="Print a single modinfo field (for example version, author, description).")
    module_group.add_argument("--params", metavar="MODULE", help="List live module parameters from /sys/module/<module>/parameters.")
    module_group.add_argument("--set-param", nargs=2, metavar=("MODULE", "KEY=VALUE"), help="Write a parameter value to /sys/module/<module>/parameters/<key>.")
    module_group.add_argument("--check-conflicts", metavar="MODULE", help="Check for common module conflicts and blacklist hints.")
    module_group.add_argument("--orphan-check", action="store_true", help="Scan for stale DKMS or orphaned module artifacts.")

    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("-E", "--enable", metavar="MODULE", help="Load a regular kernel module with modprobe.")
    action_group.add_argument("-D", "--disable", metavar="MODULE", help="Unload a regular kernel module with modprobe -r.")

    dkms_group = parser.add_argument_group("DKMS actions")
    dkms_group.add_argument("--dkms-enable", "--dkms-install", dest="dkms_enable", metavar="MODULE", help="Build and enable a DKMS module for the current kernel.")
    dkms_group.add_argument("--dkms-disable", "--dkms-remove", dest="dkms_disable", metavar="MODULE", help="Unload and remove a DKMS module for the current kernel.")

    args = parser.parse_args()

    try:
        if args.enable and args.persist:
            result = apply_module_action(args.enable, "enable")
            if result != 0:
                return result
            return persist_modules([args.enable])

        if args.persist:
            return persist_modules([args.persist])

        if args.blacklist:
            return blacklist_module(args.blacklist)

        if args.deps:
            return show_module_dependencies(args.deps)

        if args.info:
            return show_module_info(args.info)

        if args.modinfo_field:
            field, module = args.modinfo_field
            return show_modinfo_field(field, module)

        if args.params:
            return list_module_parameters(args.params)

        if args.set_param:
            module, assignment = args.set_param
            return set_module_parameter(module, assignment)

        if args.check_conflicts:
            return check_conflicts(args.check_conflicts)

        if args.orphan_check:
            return orphan_check()

        if args.dkms_enable:
            return apply_dkms_action(args.dkms_enable, "enable")

        if args.dkms_disable:
            return apply_dkms_action(args.dkms_disable, "disable")

        if args.enable:
            return apply_module_action(args.enable, "enable")
        if args.disable:
            return apply_module_action(args.disable, "disable")

        if args.list or args.list_loaded:
            names, dkms = module_names_for_listing(args.kernel)
            del names
            entries = collect_entries(args.kernel, args.search)
            if args.list_loaded:
                entries = [entry for entry in entries if entry.status == "loaded"]
            print_module_table(entries, args.kernel, args.search, dkms)
            return 0

        if not args.search and not args.list and not args.list_loaded and not args.persist and not args.blacklist and not args.deps and not args.info and not args.modinfo_field and not args.params and not args.set_param and not args.check_conflicts and not args.orphan_check and not args.enable and not args.disable and not args.dkms_enable and not args.dkms_disable:
            return run_interactive_menu(args.kernel)

        names, dkms = module_names_for_listing(args.kernel)
        del names
        entries = collect_entries(args.kernel, args.search)
        print_module_table(entries, args.kernel, args.search, dkms)
        return 0
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
