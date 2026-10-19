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
contains Board, Users, Serial console, Ethernet, Wi-Fi, SSH server and System. Each feature has
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
remove managed rule). Existing UIDs and home directories cannot be moved. Over SSH, changing
the connected login's shell or removing its groups/managed sudo access is
refused; use another administrator for those changes.
For a bootable extracted filesystem, run as root so home/key ownership
can be applied; unprivileged directory targets are for preparation/testing.
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

Autologin readback includes the selected serial getty instance as well as
the template, with drop-in filename/load-path precedence. Device aliases
such as `/dev/serial0` are resolved inside the target. If an offline image
has no device alias, a note identifies that instance-specific settings
could not be imported. Applying autologin options manages the board-tune
template drop-in; external instance overrides remain administrator-owned
and can override it.

Settings go into a `config.txt` block under the board's filter
(`[pi4]`/`[pi5]`), so they never apply to another board model.

### Wired network (`NETWORK`)

One Ethernet interface with DHCP or a static IPv4 address, gateway and
DNS servers. Uses a NetworkManager profile (Raspberry Pi OS Bookworm and
later) or a systemd-networkd file. Changes take effect on the next
boot, so a live board never drops the connection that board-tune is
using.

### Wi-Fi (`WIFI`)

With a target, the Wi-Fi menu opens a manager showing live connection/IP
status and all saved profiles. Available networks are scanned by the board
and grouped by SSID/security; cached results are labeled if scanning fails.
Select a profile to inspect or edit it, or choose Forget network. Profiles
are identified by UUID, so editing keeps the existing profile and its
unrelated settings. New/hidden networks use a masked password dialog.
Save only and forgetting non-active profiles prepare private session drafts
for the normal review/apply step after leaving menuconfig. Discarding the
session discards drafts. Drafts are not included in exported Kconfig files;
the existing WIFI_* password-file batch workflow remains available under
Batch profile / boot country options. Apply manager edits separately from
changes to those batch options. Offline targets support saved profiles
without live status or scanning.

One NetworkManager station profile per invocation: interface, UTF-8 SSID,
WPA2/WPA3 Personal, WPA3-only or open security, hidden SSID, autoconnect,
priority, DHCP/static IPv4, gateway and DNS. Existing profiles remain in
place, so repeat with another SSID to provision multiple known networks.
Enterprise/WEP/AP profiles are not imported or managed by this hook.

```config
CONFIG_WIFI=y
CONFIG_WIFI_IFACE="wlan0"
CONFIG_WIFI_SSID="Lab WiFi"
CONFIG_WIFI_PASSWORD_FILE="/home/me/private/wifi-password"
CONFIG_WIFI_COUNTRY="UA"
CONFIG_WIFI_AUTOCONNECT=y
CONFIG_WIFI_PRIORITY=100
```

Set the country to where the board actually operates; UA above is an
example. Credentials come from a private file on this computer. An empty
password-source field reuses a stored secret only for the same SSID and
interface; changing SSID requires its own credential. Profiles store only
the source path, and protected connection diffs are hidden. Generated
NetworkManager keyfiles are mode 0600. Open networks need no password;
WPA3-only profiles require protected management frames.

The target needs NetworkManager/nmcli, rfkill, and iw plus ISO country data
when changing country. Boot services set the regulatory domain/unblock the
radio before NetworkManager, then enable its Wi-Fi radio after it starts.
Saved rfkill state is updated without changing the running radio. Live
application reloads profiles but never activates a connection, restarts
NetworkManager or unblocks the current radio. Settings take effect at reboot.

The format follows the [NetworkManager keyfile reference](https://www.networkmanager.dev/docs/api/latest/nm-settings-keyfile.html).
Physical login, locale/time and radio checks are tracked in
[docs/board-checks.md](docs/board-checks.md).

### SSH server (`SSH`)

Enables the OpenSSH server at boot and sets the port, root login and
password authentication in an `sshd_config.d` drop-in. It can also add
public keys from a file on the host to a user's `authorized_keys`. On
live boards the configuration is checked with `sshd -t` before the
server is reloaded.

### System (`SYSTEM`)

Hostname (including its 127.0.1.1 hosts entry), installed IANA timezone,
UTF-8 default language (LANG), keyboard layouts/variants/options, and
systemd-timesyncd enable/disable/custom servers are configurable. Empty
identity/localisation fields keep existing values; NTP has an explicit
Keep choice. Time synchronisation disablement masks that service; no other
NTP daemon is managed.

```config
CONFIG_SYSTEM=y
CONFIG_SYSTEM_HOSTNAME="board-01"
CONFIG_SYSTEM_TIMEZONE="Europe/Kyiv"
CONFIG_SYSTEM_LOCALE="uk_UA.UTF-8"
CONFIG_SYSTEM_KEYBOARD_LAYOUT="us,ua"
CONFIG_SYSTEM_KEYBOARD_OPTIONS="grp:alt_shift_toggle"
CONFIG_SYSTEM_NTP_ENABLE=y
CONFIG_SYSTEM_NTP_SERVERS="time.example.org"
```

Timezone data and the locales package must already be installed. Other
LC_* overrides and keyboard model settings are preserved. Locale generation
runs natively on live boards; offline media gets a conditional one-shot
service that generates locales on the next boot. No target executable is
run while editing offline media. Default language affects subsequent
logins; keyboard changes are deferred until reboot/console reconfiguration.
Hostname and NTP changes are activated on live targets. The initial NTP
backend is systemd-timesyncd only; chrony and ntpd are left alone.

### Hardware interfaces (`HARDWARE`)

On Raspberry Pi 4/5, the primary header I2C bus (GPIO 2/3) and SPI0
(GPIO 7-11) each have Keep, Enable and Disable actions. An unchecked
Configure option or a Keep action leaves the corresponding settings alone.

```config
CONFIG_BOARD_RPI5=y
CONFIG_HARDWARE=y
CONFIG_HARDWARE_I2C_ENABLE=y
CONFIG_HARDWARE_SPI_ENABLE=y
```

Settings use separate, model-filtered `config.txt` blocks at the end of
the file, after existing site settings. Conditional filters and overlay
parameter scope are reset before writing base `i2c_arm`/`spi` parameters.
Original directives, device overlays, I2C camera/VideoCore interfaces and
additional buses are preserved. An explicit I2C action also writes a managed
`/etc/modules-load.d/board-tune-i2c.conf` containing `i2c-dev`; disablement
keeps this shared module available for other adapters' `/dev/i2c-*` nodes.
Other module configuration is preserved.

The menu imports explicit base boot parameters, including the `i2c` alias
and combined dtparam lines. Unknown values, includes, overlay-scoped
parameters and conditions that cannot be evaluated stay at Keep with a
diagnostic. Absent parameters also stay at Keep: absence is not proof that
a bus is disabled. This describes boot configuration, not runtime device
status; hardware overlays/HATs and their pin conflicts are not evaluated.

Changes take effect after reboot for SSH and offline media alike. There is
no live module loading, overlay activation, GPIO access or bus scanning.
Check attached hardware/pin usage before rebooting. SPI device overlays can
claim chip selects instead of exposing spidev. The base parameter format and
overlay-scope reset follow the
[Raspberry Pi Device Tree documentation](https://www.raspberrypi.com/documentation/computers/configuration.html#dt-parameters).
Additional I2C/SPI buses, baud rates and Bluetooth follow in later
hardware milestones.

GPIO 1-Wire has Keep, Enable and Disable managed overlay actions in the same
menu. Enable uses the installed `w1-gpio.dtbo` with one BCM-numbered GPIO
(2-27, default 4; not physical header pin numbers):

```config
CONFIG_BOARD_RPI5=y
CONFIG_HARDWARE=y
CONFIG_HARDWARE_ONEWIRE_ENABLE=y
CONFIG_HARDWARE_ONEWIRE_GPIO=4
```

The model-filtered `hardware_onewire` block is backed up like other boot
changes. Changing GPIO replaces its overlay rather than adding a second
master. Disable removes the managed master and leaves an empty scope-reset
block so later site directives retain their meaning. Keep changes nothing.
No kernel modules are blacklisted or unloaded; other 1-Wire drivers remain
available. Changes take effect at the next boot on SSH and offline targets.

Readback supports one standard `w1-gpio`/`w1-gpio-pi5` overlay, including
inline/colon parameters and subsequent dtparam lines. An existing external
overlay with the requested GPIO is reused without duplication; changing its
GPIO or disabling it requires explicitly editing that external configuration.
Includes, conditional/multiple masters, unsupported parameters and the
separate `w1-gpio-pullup` variant are reported as uncertain, and changes are
refused until the ambiguity is resolved or Keep is selected. Other arbitrary
overlays/HATs are not evaluated for hidden masters or GPIO usage.

Known primary I2C/SPI pin conflicts and a serial console enabled in the same
profile are rejected when configuring 1-Wire. Check other pin users and
provide the pull-up wiring required by the attached device. The historical
`pullup` overlay option is ignored by the standard overlay; this is not an
external pull-up control. See the
[official overlay reference](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm/boot/dts/overlays/README).
Controller enumeration and actual device communication remain unverified
until a physical-board check is available.

The Additional UARTs submenu configures Pi 4 UART2-5 and Pi 5 UART0-4.
Each port has Keep, Enable, Disable managed overlay and optional CTS/RTS.
GPIOs below use BCM numbering; controller IDs are not Linux tty numbers.

| Board | UART | TX/RX GPIO | CTS/RTS GPIO |
|---|---|---|---|
| Pi 4 | 2 | 0/1 | 2/3 |
| Pi 4 | 3 | 4/5 | 6/7 |
| Pi 4 | 4 | 8/9 | 10/11 |
| Pi 4 | 5 | 12/13 | 14/15 |
| Pi 5 | 0 | 14/15 | 16/17 |
| Pi 5 | 1 | 0/1 | 2/3 |
| Pi 5 | 2 | 4/5 | 6/7 |
| Pi 5 | 3 | 8/9 | 10/11 |
| Pi 5 | 4 | 12/13 | 14/15 |

GPIO 0/1 overlap HAT identification. Verify connected hardware before using
those ports. Pin assignments follow the
[official UART overlay reference](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm/boot/dts/overlays/README).
For example, enable Pi 5 UART2 and reserve its flow-control pins:

```config
CONFIG_BOARD_RPI5=y
CONFIG_HARDWARE=y
CONFIG_HARDWARE_UART2_ENABLE=y
CONFIG_HARDWARE_UART2_CTSRTS=y
```

The target must have the corresponding installed overlay: `uart2.dtbo`,
etc. on Pi 4; explicit `uart0-pi5.dtbo` through `uart4-pi5.dtbo` on Pi 5.
Managed `hardware_uartN` blocks are backed up, model-filtered and applied
at next boot; disable removes the managed overlay while retaining scope
boundaries. Keep preserves that port. A matching external overlay is reused;
changing its flow-control setting or disabling it requires explicitly editing
its configuration. Readback accepts inline/colon and subsequent dtparam
ctsrts values. Unknown parameters (including RS485), multiple overlays,
conditional/includes and external base-dtb UART controls are not silently
rewritten. Generic Pi 5 `uartN` names are accepted only when that target's
`overlay_map.dtb` explicitly maps them to the matching `uartN-pi5` overlay.
Missing, malformed or incompatible maps leave those names uncertain; parsing
runs on this computer without target executables or an extra dtc dependency.
Known base UART controls remain external, so a legacy UART0 can be preserved
while an independent UART is configured.

CTS/RTS enablement is emitted as the `ctsrts` flag; disablement omits it.
On Pi 5, even `ctsrts=off` reserves flow-control pins because that overlay
override acts on presence. Readback accounts for this and reapplying an older
managed profile with CTS/RTS disabled removes the flag completely.

Validation checks known primary I2C/SPI, GPIO 1-Wire and other UART pin
conflicts, including ports left at Keep. Primary kernel-console and enabled
serial-getty pins are protected from port changes. An unresolved tty/serial
console mapping or explicit earlycon address prevents UART changes instead
of guessing which controller it uses. The existing Serial console hook
continues to manage the primary console; Pi 4 Bluetooth/primary UARTs and
the Pi 5 debug UART are not configured by this submenu. Arbitrary device
overlays/HAT pin use still requires operator review.

No live overlays, getty restarts, device access or termios changes run.
Baud rate, parity and runtime hardware flow control belong to the application;
the CTS/RTS option reserves the pins at boot. Check runtime tty-to-controller
mapping before opening a port, particularly on Pi 4. Pi 5 enumeration,
pinmux and internal loopback results are recorded in
[docs/board-checks.md](docs/board-checks.md); external wiring and attached-device
communication remain pending.

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
`read_serial_console(ctx)` for boot-media detection and serial readback,
`serial_console_device(ctx)` for the corresponding console device name,
and `read_hardware(ctx)` / `setup_hardware(ctx)` for hardware interfaces.

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
