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
