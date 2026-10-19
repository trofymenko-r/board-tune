# Physical-board checks

Checks without a recorded result below are pending. Automated rootfs tests,
native profile parsing and unit validation do not establish real login,
radio or hardware behavior.
Use a test installation, keep the current administrator session open, and
record the board model and Raspberry Pi OS release with the result.

## Users and SSH

Create a profile on the host (choose a new, unused test username):

```config
CONFIG_USERS=y
CONFIG_USERS_NAME="btcheck"
CONFIG_USERS_SUDO_NOPASSWD=y
CONFIG_SSH=y
CONFIG_SSH_AUTHORIZED_KEYS_USER="btcheck"
```

Preview it against the test board and supply the host's public-key file:

```sh
board-tune ADMIN@BOARD --load users-check.cfg --set "SSH_AUTHORIZED_KEYS=$HOME/.ssh/id_ed25519.pub" --dry-run
board-tune ADMIN@BOARD --load users-check.cfg --set "SSH_AUTHORIZED_KEYS=$HOME/.ssh/id_ed25519.pub"
```

Open a separate connection as `btcheck`, then check:

```sh
id
getent passwd btcheck
stat -c '%a %u:%g %n' "$HOME" "$HOME/.ssh" "$HOME/.ssh/authorized_keys"
sudo -n true
```

Expected: a distinct UID, the intended primary group, home mode 750,
.ssh mode 700, authorized_keys mode 600, matching numeric ownership, key
login and passwordless sudo. Repeat the same board-tune invocation; expect
Nothing to change. Check added/replaced groups separately if requested.
To test password login, use a locally generated hash file and a separate
password-based SSH session; never paste passwords/hashes into issue output.
Verify on offline media too: apply as root or to the mounted device, boot,
and repeat the login checks. New home directories intentionally start empty.

## Identity, localisation and time

On the test installation set a hostname, an installed timezone, a supported
UTF-8 locale, keyboard layout, and timesyncd policy in a System profile.
Check the preview, apply, and verify:

```sh
hostnamectl
readlink -f /etc/localtime
cat /etc/default/locale
locale
cat /etc/default/keyboard
timedatectl
systemctl status systemd-timesyncd.service
```

Use a new login to check LANG. For an offline image, boot and inspect
`journalctl -u board-tune-locale.service`; its pending flag should disappear
after successful locale generation. Test keyboard input on an actual local
console. NTP synchronisation needs network access to a reachable server;
service enablement alone is not proof of synchronisation.

## Wi-Fi

Choose the real operating country, SSID and desired security mode. Store
the password in a private host file, then point WIFI_PASSWORD_FILE at it.
Use an Ethernet/serial administrator connection for this test so the board
remains reachable while testing its radio. Preview and apply the Wi-Fi
profile: the running connection must remain intact, with no radio activation
or NetworkManager restart. Reboot the test board and check:

```sh
systemctl status board-tune-wifi.service board-tune-wifi-enable.service
nmcli radio wifi
nmcli -f NAME,TYPE,DEVICE connection show --active
nmcli -f GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY device show wlan0
iw reg get
```

Confirm association with the chosen SSID, correct DHCP/static address,
gateway/DNS and country. Verify hidden SSID and WPA3 against an AP that
supports them. Check disabled autoconnect and profile priorities separately.
Do not include `nmcli --show-secrets` output in reports. Repeat application
and check idempotency before and after reboot.

## Hardware interfaces

Confirm the primary header pins are available before enabling I2C (GPIO 2/3)
and SPI0 (GPIO 7-11). Preserve camera/HAT overlays and use a test installation.
No i2cdetect scans or SPI transfers are needed to verify controller enablement.

```config
CONFIG_BOARD_RPI5=y
CONFIG_HARDWARE=y
CONFIG_HARDWARE_I2C_ENABLE=y
CONFIG_HARDWARE_SPI_ENABLE=y
```

Preview/apply this profile over the existing administrator connection, record
the backup ID, then reboot. Check `/dev/i2c-1`, `/dev/spidev0.0` and
`/dev/spidev0.1` (unless a device overlay claims the chip selects),
`lsmod | grep i2c_dev`, and the corresponding `/sys/bus/*/devices` entries.
Check the connected camera's enumeration before/after the reboot. Repeat
the same profile and expect no changes. Exercise Disable independently,
reboot, and check only the primary controllers disappear while camera and
other buses remain. Keep must preserve all files; no runtime commands should
appear in the preview. File restore followed by reboot should recover the
original boot/module configuration.

### GPIO 1-Wire (deferred: no test device as of 2026-10-19)

Choose a free BCM GPIO (default 4, physical header pin 7), and check wiring
and pull-up requirements for the actual device before connecting it. Use:

```config
CONFIG_BOARD_RPI5=y
CONFIG_HARDWARE=y
CONFIG_HARDWARE_ONEWIRE_ENABLE=y
CONFIG_HARDWARE_ONEWIRE_GPIO=4
```

Preview/apply, record the backup ID and reboot the test installation.
Check `/sys/bus/w1/devices/w1_bus_master*` for controller enumeration. Without
a sensor this does not establish working communication. With a known device,
confirm its family/ID directory and a valid reading through that device's
driver (for a temperature sensor, inspect `w1_slave` and its CRC). Repeat the
profile and expect no changes. Test changing to another free GPIO, then
Disable managed overlay and reboot; the managed master should disappear
while other bus/device drivers remain. Restore the original boot file when
finished. An external overlay must be edited explicitly before GPIO changes
or disablement; it is never silently removed or duplicated.

Controller enumeration, electrical behavior and sensor readings have not
been checked on a board for this milestone. Automated rootfs/live-plan tests
do not replace that check.

### Additional UARTs (Pi 5 results below; external wiring check pending)

Choose the correct controller and free TX/RX GPIOs from the table in README.
GPIO 0/1 overlap HAT identification; CTS/RTS needs two further free pins.
Example for Pi 5 (Pi 4 supports UART2-5 with different pin assignments):

```config
CONFIG_BOARD_RPI5=y
CONFIG_HARDWARE=y
CONFIG_HARDWARE_UART2_ENABLE=y
CONFIG_HARDWARE_UART2_CTSRTS=y
```

Preview/apply, record the backup ID and reboot a test installation. Confirm
the intended controller has a tty device by inspecting its sysfs/Device Tree
binding; do not infer Pi 4 controller IDs from ttyAMA numbers. Verify the
original serial console, SSH access, Bluetooth and camera remain usable.
Repeat the profile and expect no changes. With suitable 3.3 V wiring and the
correct runtime termios settings, check a TX/RX loopback or a known peripheral.
Verify CTS/RTS separately with the appropriate wiring and application flow
control enabled. A tty entry alone is not a successful communication test.

Disable the managed overlay, reboot, and verify it is absent while other
ports remain configured. Restore original boot files when finished. External
overlays/base parameters must be resolved explicitly before changing that
port. Pi 5 enumeration, pinmux, internal loopback and retained peripheral
bindings are covered by the recorded result below. External TX/RX and
CTS/RTS wiring, Pi 4 and booting offline media still need separate checks.

## Recovery

Record the printed backup ID. Use `--list-backups`, inspect a
`--restore ID --dry-run`, then restore on the test installation and reboot
or reload the affected services. Confirm persistent files recover correctly.
Native useradd/usermod/chpasswd effects are not automatically undone by
file restoration. Account deletion is outside the current hook's scope.

## Results

### 2026-10-17: Users over SSH on Raspberry Pi 5

Raspberry Pi 5 Model B Rev 1.1, Debian 12 (bookworm), aarch64,
kernel 6.12.96+rpt-rpi-2712; connected as the existing administrator `mipi`.

- Dry-run planned a new `btcheck` account and left it absent.
- The first application found a real compatibility bug: Debian useradd
  rejects `-K CREATE_MAIL_SPOOL=no`, which belongs to /etc/default/useradd,
  rather than login.defs. File rollback ran; the native groupadd effect
  remained. After removing that override, retry reused the group and succeeded.
  Mail creation now follows the target's useradd defaults.
- Created `btcheck` with UID/GID 1001, /bin/bash, and a locked password.
  A separate SSH connection succeeded with password and keyboard-interactive
  authentication explicitly disabled, proving public-key access.
- Home, .ssh and authorized_keys had modes 750, 700 and 600 respectively,
  all owned by 1001:1001. Passwordless `sudo -n true` succeeded.
- Reapplying the same profile returned `Nothing to change`.
- A fresh connection as `mipi` still had its original groups and working sudo.

The `btcheck` account, its authorized public key and its managed passwordless
sudo rule remain on the test board for further checks. The successful file
backup is `20261016T222616Z-f897b11cadec` (UTC timestamp); restoring it does
not remove the native user/group. Password-hash login, group changes, offline
boot, System settings and Wi-Fi association were not exercised in this run.

### 2026-10-17: primary I2C/SPI on Raspberry Pi 5

Same Pi 5/Debian installation and SSH administrator as above. The owner
confirmed the header GPIOs are free, with a camera on MIPI0 using the existing
`db-mipi-a1,cam0` overlay. No bus scans, transfers or live pin activation ran.

- Enable preview contained only two board-filtered config.txt blocks and the
  i2c-dev modules-load file. Reapplying before and after reboot changed nothing.
- After reboot, /dev/i2c-1, /dev/spidev0.0 and /dev/spidev0.1 appeared;
  i2c_dev was loaded. Existing camera I2C clients, their driver bindings and
  /dev/video* and /dev/media* lists matched their pre-test state exactly.
- The initial disable test exposed removal of the shared i2c-dev boot file:
  controller/driver entries survived, but other adapters' userspace nodes did
  not. Fixed explicit I2C actions to retain shared i2c-dev boot loading.
- With that correction, a further disable/reboot removed only /dev/i2c-1
  and spidev0.0/0.1. Original /dev/i2c-10, -13, -14, -6 and spidev10.0
  remained, with unchanged camera/media devices and working administrator sudo.
  Reapplying the disable profile changed nothing.
- Restored the original boot/module files from backup
  `20261016T223712Z-623362a804d0`. Restore created safety backup
  `20261016T224009Z-97763a759ea6`. The primary buses were already inactive,
  so no further reboot followed the file restore; shared i2c-dev remained
  loaded as in the pre-test runtime. Backup files remain for review.

This establishes controller/device enumeration, not communication with an
attached I2C/SPI peripheral. `rpicam-hello --list-cameras` reported no cameras
before any change; image capture through the custom MIPI driver was not tested.
Pi 4 hardware and booting offline media still require their own checks.

### 2026-10-19: UART1-4 on Raspberry Pi 5

Pi 5 Model B Rev 1.1, Debian 12, kernel 6.12.96+rpt-rpi-2712, SSH as `mipi`.
Initial config had generic `dtoverlay=uart0`, `dtparam=uart0=on`,
`db-mipi-a1,cam0`, ttyAMA0 on the header and serial0/getty on ttyAMA10.
GPIO 0-13 were unused; UART0 owned 14/15. The owner had confirmed free header
pins and no UART peripherals were attached for this test.

- First preview exposed the unsupported generic alias/base-control combination.
  Added bounded FDT v17 reading of the target's overlay_map.dtb. Its exact
  bcm2712 mappings verified uart0-4 aliases; legacy UART0 remained unchanged.
- Initial enablement produced UART1-3 but UART4 failed binding: its generated
  `ctsrts=off` still requested GPIO 14/15, already owned by UART0. Confirmed
  this in kernel pinctrl errors, the installed overlay override and live DT.
  Fixed disabled flow control to omit the parameter, then reapplied/rebooted.
- UART1-4 appeared as ttyAMA1-4, verified against their RP1 of_node bindings
  and Device Tree aliases. Pinmux showed TX/RX for all four, CTS/RTS for
  UART1-3, and preserved UART0 TX/RX. UART4 flow control was correctly refused
  in a dry-run because it overlaps UART0.
- Used PL011 internal loopback via TIOCM_LOOP, after verifying the loop bit
  before sending bytes. Every port echoed a 1024-byte binary pattern exactly
  at 9600, 115200 and 921600 baud, 8N1. UART1-3 also passed a 1024-byte exchange
  at 115200 with CRTSCTS enabled: 15 exchanges, 15360 bytes in total.
  The script restored termios/modem settings in finally blocks. This tests
  controller/driver loopback; it does not test external signal wiring.
- Disabling CTS/RTS, then rebooting, released GPIO 2/3, 6/7 and 10/11 while
  all four tty devices remained. Disabling managed UART1-4 and rebooting
  removed those tty devices; ttyAMA0 and ttyAMA10 stayed present. Same-profile
  application returned Nothing to change before/after reboot and after each
  transition. Real-target readback imported enabled ports and flow settings.
- Camera video/media nodes, camera I2C client names/driver bindings, I2C/SPI
  controllers, Bluetooth hci enumeration, active Bluetooth/getty services,
  serial0 binding and kernel-command-line file matched the initial snapshot.
- Restored original config.txt from `20261019T193651Z-91c219c47e78`; safety
  backup `20261019T194247Z-74ce01f1e7eb` retains the disabled managed state.
  Verified exact file bytes/mode/UID/GID and GPIO 0-17 against the baseline.
  No additional reboot followed file restore: the already-disabled runtime
  matched the original header state. Backups remain available for review.

External jumper/peripheral TX/RX and CTS/RTS handshakes, camera image capture,
Bluetooth pairing, primary-console electrical communication, Pi 4 and
offline-media boot were not exercised. The driver's internal loopback support
is documented in the
[PL011 kernel source](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/drivers/tty/serial/amba-pl011.c).

## Serial autologin readback correction (2026-10-19)

On `mipi@192.168.0.11`, `console=serial0,115200` resolves to ttyAMA10.
Autologin is configured by the administrator's
`/etc/systemd/system/serial-getty@ttyAMA10.service.d/autologin.conf`, rather
than a template drop-in. The former reader missed this instance override.
The corrected SSH readback returns `SERIAL_CONSOLE_AUTOLOGIN=y` and
`SERIAL_CONSOLE_AUTOLOGIN_USER=mipi`, agreeing with the active service's
ExecStart and DropInPaths. SHA-256 checks of config.txt, cmdline.txt and
the instance override matched before/after. No files were applied, no
getty was restarted and no reboot was needed.

## Wi-Fi manager inventory (2026-10-19)

Read-only SSH checks found all three saved profiles with distinct UUIDs;
wireless90-5G was correctly ordered first as the active wlan0 connection.
Status returned its actual 192.168.1.156/24 address, gateway and DNS. No
activation, disconnect, deletion or forced scan was performed. A real PTY
check also opens/closes the offline profile browser without changing files.

Fresh on-board AP scanning returned eight access points, including a hidden
SSID. wlan0 remained connected to the same profile before/after the scan.
Profile edit/forget behavior is tested on disposable rootfs fixtures,
including backup restoration, duplicate SSIDs, external concurrent edits,
secret redaction and discarding a forget draft in a real PTY. The board's
saved profiles were not modified for these checks.

## Wi-Fi manager live transactions (2026-10-19)

Pi 5 / NetworkManager 1.42.4, with the controller connected through
Ethernet end0 at 192.168.0.11. wlan0's original profile was wireless90-5G,
UUID 9ef99d92-7e32-42d7-a18c-7a16e36b098c, IP 192.168.1.156/24.

- Native per-wlan0 checkpoint creation/destruction passed before activation.
- Activating/keeping the existing profile passed without changing its files.
- A temporary profile with a new UUID, stored key, autoconnect disabled and
  priority -999 associated with the same AP. Closing its SSH controller
  without confirmation exercised the independent worker timeout: old
  runtime/profile returned, the new profile was removed, status was reverted.
- A second temporary profile passed explicit Keep. Forgetting that active
  profile followed by Revert restored both its bytes and active connection.
  Repeating Forget with Keep removed only the temporary profile. The initial
  profile was explicitly reactivated over Ethernet afterward.
- A random nonexistent open SSID timed out activation after 25 seconds,
  reported the NetworkManager failure, removed its trial profile and
  restored the original connection. This check first exposed NM silently
  ignoring an empty wifi-security section; open profile rendering now omits
  that section and legacy security references, with a regression test.
- Compared all three original profile paths, exact contents, permissions,
  owner/group and active UUID after restoration. They matched the baseline.
  Ethernet SSH stayed available. Temporary requests/scripts are private
  under /run; request credentials are removed by the worker on completion.
- A real SSH PTY opened status, all saved UUIDs, the Forget action and fresh
  AP results, then exited with Nothing to change. Offline PTY tests verify
  masked password input, cancellation, discarding a forget draft and applying
  a forget draft only after the normal preview/confirmation.

Retained file backup IDs include 20261019T204449Z-7f63c6b882b9 (timeout),
20261019T204554Z-d2bce737d7f8 (keep), 20261019T204607Z-9840c6ffe374 (forget/
revert), 20261019T204608Z-b4ab2d780d54 (forget/keep), and
20261019T204821Z-817403a1e62a (unavailable AP). Private backups intentionally
retain the recoverable profile data.

Wrong-password authentication against the real AP, hidden-AP association,
regulatory/radio boot changes, lack of Internet, and loss of a session
actually transported over Wi-Fi remain unverified. Controller disappearance
was tested with independent Ethernet access, not Wi-Fi SSH address migration.

Final inspection found exactly the three original Wi-Fi profiles, the original
active UUID, no outstanding checkpoints and no request.json credential files.
DHCP assigned wlan0 192.168.1.157/24 after reconnection; Ethernet SSH remains
192.168.0.11/24. Restoring a DHCP profile does not pin its former lease address.
