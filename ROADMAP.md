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
| 1 | Users: creation, password/hash, password locking, shell, groups, sudo | Pending |
| 2 | Hostname, timezone, locale, keyboard layout, NTP/time servers | Pending |
| 3 | Wi-Fi: SSID, credentials, country, hidden SSID, autoconnect, priorities | Pending |
| 4 | Hardware: I2C, SPI, 1-Wire, additional UARTs, overlays/parameters, Bluetooth | Pending |
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
- New user login, SSH key access, password policy and sudo behavior.
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
