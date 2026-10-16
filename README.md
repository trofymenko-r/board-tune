# board-tune

Hook-based customization of Linux boards, either live over SSH or
offline through their boot media. Options are chosen in a menuconfig
interface or loaded from Kconfig-style profiles.

Supported boards: Raspberry Pi 4 and 5 (including Pi 400/500 and the
Compute Modules) with Raspberry Pi OS Lite or Desktop. The core is
platform-agnostic; other boards are added as platforms.

The agreed implementation plan and outstanding board checks are tracked in
[ROADMAP.md](ROADMAP.md).

## Installation

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
source .venv/bin/activate
```

This installs the `board-tune` command. The host must run Linux;
offline targets additionally need `sudo`, `lsblk` and `losetup`.

## Usage

```
board-tune [TARGET] [--load FILE]... [--set SYMBOL=VALUE]... [--save FILE]
           [--menu] [-n|--dry-run] [-y|--yes]
```

| Command | Effect |
|---|---|
| `board-tune /dev/sdb` | Read current settings, open the menu, then show and confirm changes |
| `board-tune pi@192.168.1.50` | Read current settings and detect the board over SSH, then open the menu |
| `board-tune pi@192.168.1.50 --load rpi5.cfg` | Batch mode: no menu, invalid settings are errors |
| `board-tune pi@192.168.1.50 --load rpi5.cfg --menu` | Menu with current settings overridden by the profile |
| `board-tune --save rpi5.cfg` | Menu without a target: only save a profile |
| `board-tune --load base.cfg --load eth.cfg --save lab.cfg` | Merge profiles into a new one |
| `board-tune raspios.img --load rpi5.cfg --dry-run` | Show what would change in an image |

With a target, the menu starts from settings read from the board's files:
serial console and UART, Ethernet profiles, and global SSH settings.
`--load` profiles override those values, followed by `--set`. Batch mode
without `--menu` continues to apply only the supplied profile and defaults.
An unchanged hook is left untouched when exiting the target's menu, so
viewing existing settings does not normalize or rewrite its files.

A live board's model is detected from its device tree. A stock offline
image may support both Pi 4 and Pi 5 and cannot identify its intended
board. Check the Board selection, or provide `--set BOARD_RPI5=y` before
opening the menu. Media with a board-tune serial-console block can recover
the model from that block.

The menu shows stored configuration, not runtime information such as a
DHCP lease or whether sshd is currently running. It imports one Ethernet
profile and global SSH directives (including `Include` files); SSH `Match`
rules and UART settings in firmware include files are not imported.
Unrecognized settings keep their menu defaults and are left untouched
unless that hook's options are edited. A local source path for existing
`authorized_keys` cannot be recovered and remains empty.

The menu uses a compact gray dialog on a blue background, with the same
checkboxes, choices and button layout as kernel menuconfig. The top level
contains Board, Users, Serial console, Ethernet and SSH server. Each feature has
an explicit Configure checkbox; advanced boot and network options are in
submenus, and public keys have their own SSH submenu.

Use Up/Down to select an item, Enter to open or edit it, Space (or Y/N) to
change a checkbox, and Esc to return. Tab or Left/Right selects the bottom
buttons. Help shows a short explanation of the selected option. Save keeps
settings for the current session; Exit continues to the change preview.
Load imports a profile without changing that file. To export a profile,
use `--save FILE`. Dialogs resize with the terminal (minimum 60 columns by
14 rows). `MENUCONFIG_STYLE` can override the default colors.

Before anything is written, board-tune shows every file it is about to
create, modify or remove, with a diff.

### Backups and recovery

Each application stores a private file snapshot under
`/var/backups/board-tune` on the target. Backups include original file
contents, permissions, ownership, symlinks and the absence of new paths.
Sensitive diffs are hidden; backup directories are mode 0700 and their
manifests are mode 0600. Preview and aborted confirmation create no backup.

```sh
board-tune pi@192.168.1.50 --list-backups
board-tune pi@192.168.1.50 --restore BACKUP_ID --dry-run
board-tune pi@192.168.1.50 --restore BACKUP_ID
```

A write or command failure triggers restoration of the snapshotted files.
A manual restore also backs up the current files first. This is file
recovery: runtime effects of commands, service state, concurrent changes
and automatically created parent directories are not a filesystem
transaction. Reload affected services or reboot after manual recovery.
If recovery fails, its backup ID and errors are reported for manual action.
Backups are retained until the operator removes them.

### Targets

- **`[user@]host`**: a running board, reached with the system `ssh`
  client, so `~/.ssh/config`, agents and jump hosts work. The user must
  be root or have passwordless `sudo` on the board.
- **Block device** (`/dev/sdb`, `/dev/mmcblk0`): the board's SD card or
  USB drive. Only removable disks are accepted, and disks with
  partitions mounted outside `/media`, `/run/media` or `/mnt` are
  refused. Partitions the desktop has already mounted are reused.
- **Disk image** (`*.img`): attached with `losetup`.
- **Directory**: a root filesystem that is already extracted or mounted.

Offline targets find the root filesystem by its `/etc/os-release` and
mount the other partitions where the image's `/etc/fstab` puts them.
Nothing on an offline target is executed, and symlinks are resolved
inside the target, never on the host.

### Configuration files

Profiles use the Kconfig `.config` format. They only need the options
that differ from the defaults, which is what `--save` writes:

```
CONFIG_BOARD_RPI5=y
CONFIG_SERIAL_CONSOLE=y
CONFIG_SERIAL_CONSOLE_AUTOLOGIN=y
CONFIG_NETWORK=y
CONFIG_NETWORK_STATIC=y
CONFIG_NETWORK_ADDRESS="192.168.1.50/24"
CONFIG_NETWORK_GATEWAY="192.168.1.1"
CONFIG_SSH=y
CONFIG_SSH_PASSWORD_AUTH_NO=y
```

Several `--load` files are merged in order, and `--set` overrides
single values (`--set NETWORK_ADDRESS=192.168.1.51/24`). In batch
mode, unknown symbols and values whose dependencies are not met (for
example a Pi 4 option in a Pi 5 profile) are errors instead of being
silently dropped.

## Hooks

A hook whose menu entry is disabled leaves the target untouched. When
applied, an enabled hook fully manages the files it owns; for example, turning off
autologin removes the autologin drop-in again.

### Users (`USERS`)

Create or update one local user per profile. Options cover create-if-missing,
login shell, supplemental groups (keep/add/replace), password policy
(keep/lock/set hash), and a per-user sudo rule (keep/password/passwordless/
remove managed rule). Existing UIDs and home directories cannot be moved.
New users get a free UID/GID from the target's login.defs range, a private
primary group and an empty mode-0750 home. Other users are preserved.

```config
CONFIG_USERS=y
CONFIG_USERS_NAME="operator"
CONFIG_USERS_GROUPS_ADD=y
CONFIG_USERS_GROUPS="dialout"
CONFIG_USERS_SUDO_NOPASSWD=y
CONFIG_SSH=y
CONFIG_SSH_AUTHORIZED_KEYS="/home/me/.ssh/id_ed25519.pub"
CONFIG_SSH_AUTHORIZED_KEYS_USER="operator"
```

New accounts start with a locked password. To set one, generate a hash with
`openssl passwd -6` into a private file on this computer, select
`USERS_PASSWORD_HASH=y` and set `USERS_PASSWORD_HASH_FILE` to that file.
Profiles store the source path, never the hash itself; shadow/gshadow diffs
and command stdin are hidden. Locking a password does not remove SSH keys.
The menu selects an existing ordinary user when possible; all user actions
start at Keep, so changing the selected name cannot copy another identity.

On live targets account operations use shadow-utils (useradd/groupadd/
usermod/chpasswd), which take the native database locks. New metadata is
available to later SSH/serial hooks within the same plan. Live account
commands are not automatically reversed by file recovery; inspect the
reported commands after a failure. Offline targets edit only their own
passwd/group/shadow/gshadow files through the backed-up plan.

Sudo rules use `/etc/sudoers.d/90-board-tune-USER`. The main sudoers must
include that directory; live rules are checked through visudo before
installation. Removing a managed rule does not revoke grants from other
files or groups. Groups must exist before adding memberships. Verify real
login and sudo behavior on a test board before relying on a new account.

### Serial console (`SERIAL_CONSOLE`)

- Puts the kernel console on the serial port at the configured baud
  rate, as the primary console (`/dev/console`) or next to the display.
- Full boot log: removes `quiet` and `splash` and sets `loglevel`.
- Optional autologin for a user on any serial getty.
- Raspberry Pi 4: mini UART, or the PL011 with Bluetooth disabled.
- Raspberry Pi 5: the debug UART connector, or UART0 on GPIO 14/15.
- Optional firmware diagnostic output (`uart_2ndstage=1`). The
  bootloader's own log is enabled with `BOOT_UART=1` in the EEPROM
  configuration, which is not on the boot media.

Settings go into a `config.txt` block under the board's filter
(`[pi4]`/`[pi5]`), so they never apply to another board model.

### Wired network (`NETWORK`)

One Ethernet interface with DHCP or a static IPv4 address, gateway and
DNS servers. Uses a NetworkManager profile (Raspberry Pi OS Bookworm and
later) or a systemd-networkd file. Changes take effect on the next
boot, so a live board never drops the connection that board-tune is
using.

### SSH server (`SSH`)

Enables the OpenSSH server at boot and sets the port, root login and
password authentication in an `sshd_config.d` drop-in. It can also add
public keys from a file on the host to a user's `authorized_keys`. On
live boards the configuration is checked with `sshd -t` before the
server is reloaded.

## Extending

Hooks and platforms are directories with an `__init__.py`:

```
hooks/<name>/__init__.py      HOOK = MyHook()       (subclass of registry.Hook)
hooks/<name>/Kconfig          options, gated by the hook's symbol
platforms/<name>/__init__.py  PLATFORM = MyPlatform()  (subclass of registry.Platform)
platforms/<name>/Kconfig      optional platform options
platforms/<name>/hooks/<hook>.Kconfig   board options shown in a hook's menu
```

Besides the built-in ones in `board_tune/`, they are loaded from the
directories listed in `BOARD_TUNE_PATH` (colon-separated, each with
`hooks/` and/or `platforms/` subdirectories).

A hook can implement `read(ctx)` to return a mapping of Kconfig symbol
names to current string values (`"y"`/`"n"` for booleans), without staging
changes. The default returns nothing, preserving existing extensions.
Platforms can implement `detect_offline_board(target)` and
`read_serial_console(ctx)` for boot-media detection and serial readback.

A hook's `plan(ctx)` reads and stages changes through `ctx.plan`
(`read`, `write`, `mkdir`, `symlink`, `remove`, `command`, `note`) and
never touches the target directly. Staging what is already on the
target is not a change, so hooks are idempotent as long as they
compute the desired state. Board-specific work goes through
`ctx.platform`; a platform that lacks a service raises `Unsupported`.

## Testing

```sh
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

The disk image test needs root to attach loop devices and is skipped
otherwise:

```sh
sudo .venv/bin/pytest tests/test_block.py
```

## Limitations

- One local user is managed per profile; account deletion and UID/home migration are not supported.
- Only one wired interface can be configured.
- Network changes on live boards are not activated until a reboot.
- The Raspberry Pi 5 GPIO UART option relies on the documented
  `uart0_console` parameter and has not been tested on hardware yet.
