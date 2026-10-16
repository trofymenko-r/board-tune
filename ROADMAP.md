# board-tune roadmap

Agreed with the project owner on 2026-10-17. This file is the durable project
plan: update status and verification notes in every implementation commit.
Implement in stages, commit each completed stage, and ask the owner when a
physical-board check is needed. Do not mark hardware checks complete based
on synthetic rootfs tests. Ask questions when a decision cannot reasonably
be inferred from the existing design.

## Completed baseline

- Raspberry Pi 4/5 platform, including corresponding Pi 400/500 and CM variants.
- Serial console: baud, primary console, boot verbosity, kernel log level,
  autologin/user, model-specific UART selection, firmware diagnostics.
- One Ethernet interface: DHCP/static IPv4, prefix, gateway, DNS,
  NetworkManager/systemd-networkd.
- SSH server: boot enablement, port, root/password login, public-key import.
- Profiles, profile merging, overrides, target readback, preview/dry-run.
- Compact kernel-style menuconfig interface.

## Delivery order

| Stage | Scope | Status |
|---|---|---|
| 0 | File backups and recovery foundations for subsequent changes | Implemented; 111 tests pass, 1 root-only test skipped |
| 1 | Users: creation, password/hash, password locking, shell, groups, sudo | Implemented; Pi 5 SSH-key login, locked password, sudo and repeat application verified |
| 2 | Hostname, timezone, locale, keyboard layout, NTP/time servers | Implemented; 140 tests pass, 1 root-only test skipped |
| 3 | Wi-Fi: SSID, credentials, country, hidden SSID, autoconnect, priorities | Implemented for NetworkManager; physical radio/AP check pending |
| 4 | Hardware: I2C, SPI, 1-Wire, additional UARTs, overlays/parameters, Bluetooth | Primary I2C/SPI implemented and verified on Pi 5; remaining interfaces pending |
| 5 | Boot: console/desktop target, network wait, splash, local console autologin | Pending |
| 6 | Networking: multiple interfaces, IPv6, routes, metrics, MTU | Pending |
| 7 | Logging/recovery: persistent/RAM journal, size limits, hardware watchdog | Pending |
| 8 | Application services: explicit enable/disable, application autostart, restart policy | Pending |
| 9 | Use-case-specific modes: read-only rootfs/overlayfs, kiosk/display, USB gadget | Pending |

Stages 1-4 are the nearest functional milestones. Stages 5-8 follow them;
stage 9 needs concrete use-case requirements before selecting defaults.
Backup/recovery work is a cross-cutting foundation, not a substitute for
these functional milestones.

## Existing gaps to address alongside the stages

- Explicit distinction between leaving a feature unchanged and disabling it.
- More complete readback: firmware includes and SSH Match rules.
- Report ambiguous/unsupported settings instead of presenting guesses as facts.
- Validate target configurations before activation and preserve idempotency.
- Keep secrets out of displayed diffs and ordinary profile values.
- Network changes currently wait for reboot; any live activation needs a
  connectivity-preserving design before implementation.

## Physical-board verification pending

- Pi 4/Pi 5: SSH target and SD-card/image workflows.
- Pi 5: GPIO UART (`uart0_console`) and debug connector.
- Users: password-hash login, group changes and offline-media boot. New-user
  SSH-key login, locked-password status and passwordless sudo passed on Pi 5.
- Wi-Fi association, country setting and reconnect behavior.
- Enabled buses, overlays and watchdog on the corresponding hardware.

## Stage completion criteria

- Menu options, profile interface and target readback are documented.
- Applicable SSH and offline behavior is covered by meaningful automated tests.
- A disabled Configure checkbox leaves that feature alone.
- Preview, repeated application and failure handling are verified.
- Tests pass; one or more focused commits are created.
- Outstanding board checks and the next stage remain recorded here.

## Stage 0 verification

Private snapshots, symlink destination recovery, permissions, automatic
file rollback after injected failures, restore preview, and reversible
manual restoration pass tests. File recovery does not undo runtime effects
or changes made by concurrent processes. SSH/physical-media confirmation
remains on the hardware checklist.

## Stage 1 verification and next action

Implemented one local user per profile, UID/GID allocation, shell selection,
supplemental groups, locked/hash-file password policy, and managed sudo
rules. Tests cover offline databases, idempotency, dry-run, secret
redaction, SSH-key composition and native live command planning/stdin.
Live tools take their own locks; file snapshots do not undo native account
commands. New homes are empty (no /etc/skel import); no account deletion or
UID/home migration is performed.

The owner supplied a test-board SSH destination. On 2026-10-17, a Pi 5 running
Debian 12 passed new-user key login, locked-password status, passwordless sudo,
numeric ownership/modes, repeat application and original-administrator access.
The first attempt exposed Debian's rejection of `-K CREATE_MAIL_SPOOL=no`;
removed that unsupported login.defs override and successfully retried using
the group left by the native command. Mail creation follows target defaults.
Detailed results and the retained test account are recorded in
docs/board-checks.md. Password-hash login, group changes and offline boot
remain unverified on hardware.

## Stage 2 verification and next action

Hostname/hosts preservation, timezone links, locale generation scheduling,
keyboard configuration and timesyncd keep/enable/disable pass tests on
temporary rootfs targets. Live activation commands are tested with a fake
transport. First boot locale generation, console keyboard application and
actual NTP synchronisation still need board verification. Locale data,
locales/keyboard packages and the NTP service are not installed by this hook.
The next independent functional stage is Wi-Fi.

Stage 1 access guard: record the SSH login UID and reject changing its shell
or removing its supplementary groups/managed sudo grant during that same
session. Unprivileged extracted-directory workflows report that ownership
must be applied as root before boot.

## Stage 3 verification and next action

Wi-Fi keyfile tests cover WPA2/WPA3/open security, SSID escaping, private
credentials, stored-secret reuse, country validation, hidden/autoconnect/
priority settings, DHCP/static addresses and idempotency. Generated samples
were also accepted by the host libnm parser and normalizer without contacting
NetworkManager. Both boot services passed systemd-analyze verify. Runtime
activation is deferred until reboot; live command tests confirm no restart,
connection activation or radio unblock occurs during application.

Physical checks and owner-run commands are in docs/board-checks.md. The next
functional stage is hardware interfaces; pin usage and real device behavior
must be verified on the corresponding board before relying on them.

## Stage 4: primary I2C/SPI

Implemented Keep/Enable/Disable for the primary header I2C and SPI0 buses,
model-filtered config.txt blocks, shared i2c-dev boot loading, explicit-parameter
readback and SSH/offline plans without runtime pin/module commands. Includes,
overlay parameter scope and unknown firmware conditions are reported as
uncertain rather than inferred to mean disabled. Hardware-generated blocks
also identify the board family on otherwise ambiguous offline media.

Tests cover preservation, both board families, repeated application, late
site overrides, disablement, readback ambiguity, old boot locations, dry-run
and real terminal menu editing. Full suite: 175 passed, 1 root-only skip.
On the owner's Pi 5, enabling and disabling both controllers passed across
reboots, with original camera/media devices and administrator access intact.
The owner confirmed all header GPIOs are free and a camera is attached to MIPI0.
Disabling the primary I2C controller keeps i2c-dev loaded for camera/other
adapters; the physical disable test exposed and corrected removal of that
shared module's boot file.
Original boot/module files were restored from backup after the test; actual
bus transfers, camera image capture and Pi 4 hardware remain untested.
Remaining stage-4 work: 1-Wire, additional UARTs, overlays/parameters and
Bluetooth. Baud rates/additional I2C/SPI buses are not part of this milestone.
