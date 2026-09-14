\# ⚡ ezmodule

A lightweight Linux kernel module manager for listing, inspecting, enabling, disabling, and troubleshooting kernel modules and DKMS-backed drivers.

## Features

- Browse available and loaded kernel modules
- Filter by name with `-S` / `-F`
- Show only loaded modules with `--list-loaded`
- Load and unload regular modules with `-E` / `-D`
- Install/remove DKMS drivers with `--dkms-enable` / `--dkms-disable`
- Persist modules across boot with `--persist`
- Blacklist modules with `--blacklist`
- Show dependency trees with `--deps`
- View module metadata with `--info`
- Query a single modinfo field with `--modinfo-field FIELD MODULE`
- Inspect live module parameters with `--params` and `--set-param`
- Check for likely module conflicts with `--check-conflicts`
- Detect stale DKMS/module artifacts with `--orphan-check`
- Launch a Rich-based interactive TUI with no arguments

## Installation

Follow these steps exactly from the project root.

### Option 1: install the script system-wide

1. Change into the project directory:

```bash
cd /path/to/ezmodule
```

2. Copy the script into a directory on your PATH:

```bash
sudo install -m 0755 ezmodule.py /usr/local/bin/ezmodule
```

3. Verify it works:

```bash
ezmodule --help
```

This makes the command available anywhere in your shell as `ezmodule`.

### Option 2: install as a Python package

1. Change into the project directory:

```bash
cd /path/to/ezmodule
```

2. Install it with pip:

```bash
python -m pip install .
```

3. Confirm the command is available:

```bash
ezmodule --help
```

This installs the package and exposes the `ezmodule` command.

### Option 3: editable install for development

1. Change into the project directory:

```bash
cd /path/to/ezmodule
```

2. Install it in editable mode:

```bash
python -m pip install -e .
```

3. Confirm it works:

```bash
ezmodule --help
```

This keeps the project linked to your local checkout while allowing live edits.

### If `ezmodule` is not found

If the command still does not resolve, ensure your Python user scripts directory is on PATH:

```bash
python -m site --user-base
```

Then add the generated user bin directory to your shell PATH, usually:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

If you installed with `pip install --user`, use that PATH entry.

## Usage

### List modules

```bash
ezmodule --list
```

Only loaded modules:

```bash
ezmodule --list-loaded
```

Filter by name:

```bash
ezmodule -F mt79
```

### Enable/disable a regular module

```bash
sudo ezmodule -E mt7925e
sudo ezmodule -D mt7925e
```

### Install and remove a DKMS module

```bash
sudo ezmodule --dkms-enable zenpower5
sudo ezmodule --dkms-disable zenpower5
```

### Inspect a module

```bash
ezmodule --info mt7925e
ezmodule --modinfo-field version mt7925e
ezmodule --deps mt7925e
ezmodule --params mt7925e
```

### Persist a module for boot

```bash
sudo ezmodule --persist vfio
```

### Blacklist a module

```bash
sudo ezmodule --blacklist nouveau
```

### Check for conflicts

```bash
ezmodule --check-conflicts nvidia
```

### Detect stale DKMS artifacts

```bash
ezmodule --orphan-check
```

### Launch the interactive TUI

```bash
ezmodule
```

Use the arrow keys to navigate, type to filter, press Enter to select a module, and choose an action from the action menu.

## Requirements

- Python 3.12+
- Linux kernel module utilities (`modprobe`, `modinfo`)
- Optional but recommended:
  - `dkms`
  - `rich`
  - `questionary`

## Notes

- This tool is currently known to work on Arch Linux and has not been tested on other distributions.
- Some actions require root privileges and should be run with `sudo`.
- DKMS operations depend on the module being correctly registered with `dkms status` for your current kernel.
- Some DKMS modules may fail to build against newer kernels and will report the build log path for troubleshooting.

## License

This project is provided as-is for personal and development use.
