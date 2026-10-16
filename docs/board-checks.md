# Physical-board checks

These checks are pending. Automated rootfs tests, native profile parsing and
unit validation do not establish real login, radio or hardware behavior.
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

## Recovery

Record the printed backup ID. Use `--list-backups`, inspect a
`--restore ID --dry-run`, then restore on the test installation and reboot
or reload the affected services. Confirm persistent files recover correctly.
Native useradd/usermod/chpasswd effects are not automatically undone by
file restoration. Account deletion is outside the current hook's scope.

## Results

No physical-board results have been reported yet. Add model, OS version,
transport/media, tested stages and observed outcomes here when available.
