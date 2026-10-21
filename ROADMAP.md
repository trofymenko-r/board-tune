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

Serial autologin readback corrected on 2026-10-19: resolve the console
device alias and import instance-specific systemd overrides with drop-in
precedence. Verified on Pi 5 over SSH: active autologin for `mipi` is now
shown correctly; boot/getty file hashes stayed unchanged. Regression tests
cover instance/template ordering, unrelated consoles and unchanged-menu
preservation. External instance overrides remain administrator-owned.

## Delivery order

| Stage | Scope | Status |
|---|---|---|
| 0 | File backups and recovery foundations for subsequent changes | Implemented; 111 tests pass, 1 root-only test skipped |
| 1 | Users: creation, password/hash, password locking, shell, groups, sudo | Implemented; Pi 5 SSH-key login, locked password, sudo and repeat application verified |
| 2 | Hostname, timezone, locale, keyboard layout, NTP/time servers | Implemented; 140 tests pass, 1 root-only test skipped |
| 3 | Wi-Fi: SSID, credentials, country, hidden SSID, autoconnect, priorities | Manager implemented; Pi 5 scan/connect/rollback/forget verified; country/radio/wrong-password checks pending |
| 4 | Hardware: I2C, SPI, 1-Wire, additional UARTs, overlays/parameters, Bluetooth | I2C/SPI and UART1-4 internal loopback verified on Pi 5; 1-Wire/external UART wiring pending; overlays/Bluetooth pending |
| 5 | Boot: console/desktop target, network wait, splash, local console autologin | Pending |
| 6 | Networking: multiple interfaces, IPv6, routes, metrics, MTU | Pending |
| 7 | Logging/recovery: persistent/RAM journal, size limits, hardware watchdog | Pending |
| 8 | Application services: explicit enable/disable, application autostart, restart policy | Pending |
| 9 | Use-case-specific modes: read-only rootfs/overlayfs, kiosk/display, USB gadget | Pending |

Stages 1-4 are the nearest functional milestones. Stages 5-8 follow them;
stage 9 needs concrete use-case requirements before selecting defaults.
Backup/recovery work is a cross-cutting foundation, not a substitute for
these functional milestones.

Wi-Fi manager extension approved on 2026-10-19:
active connection/status, all saved profiles, on-board AP scanning, profile
editing and guarded live connection changes. The current single-profile
readback showed Sweet Home while wireless90-5G was active on Pi 5.
See [interaction plan](docs/wifi-manager-proposal.md). Increment 1 adds
active-profile readback and a saved-profile/status browser in the existing
menu. Pi 5 SSH inspection now imports wireless90-5G first and lists all
three profiles without activation. Increment 2 adds on-board AP scanning,
SSID/security grouping, profile editing with UUID/unknown-setting retention,
masked credentials and forgetting non-active profiles through the backed-up
review/apply plan. Increment 3 adds separate Connect now / Save only actions,
guarded active-profile forgetting and an independent 60-second board-side
worker with NetworkManager checkpoint and private file recovery. Drafts can
be discarded independently; dry-run blocks live actions. Pi 5 verification
passed activation/keep, controller-loss timeout rollback, active forget keep/
revert, and unavailable-AP recovery. Original Wi-Fi profiles and active UUID
were restored. Physical wrong-password, no-Internet and Wi-Fi-carried SSH-loss
checks remain; the controller-loss test used independent Ethernet access.
Final automated verification: 319 passed, 1 root-only test skipped.
2026-10-20 presentation update: saved networks, available networks and AP
details use aligned tables with fixed headers, right-aligned numeric fields,
terminal-width adaptation and Unicode-aware clipping. Signal percentages
also have a four-step rising Wi-Fi indicator (▂▄▆█) in the network/AP lists
and active status: red below 40%, yellow at 40-69%, green from 70%. Only
filled bars are colored, retaining the normal/selected row background.
Monochrome terminals, the monochrome theme and NO_COLOR retain plain bars.
Focused Wi-Fi/menu verification: 29 passed. SSH PTY table/bar rendering and
resize passed at 60/80/100 columns, exiting with Nothing to change.
Data selection and network actions are unchanged.

## Network power saving (2026-10-21)

Ethernet Advanced offers Keep / Disable / Enable / Remove for per-interface
EEE. Persistent device-bound systemd policy plus an NM activation dispatcher
supports both network backends and older NM releases. No live link change
is scheduled; live hardware support is checked first. Power-only edits
preserve existing IP profiles. Wi-Fi profile editor and batch configuration
offer Keep / Disable / Enable / NM default / Ignore, preserving unknown and
unrelated settings. Details separates saved Wi-Fi policy from actual driver
state. Terminal review/apply, idempotency, removal, ownership and unsupported
driver guards are covered. Full suite: 353 passed, 1 root-only skip.

Pi 5/NM 1.42.4 accepted all four explicit Wi-Fi values in a temporary inactive
profile; original profile contents/metadata, active UUID and driver power-save
state remained unchanged. Its Ethernet driver reports EEE unsupported and
the new live guard correctly refuses application. EEE boot/hotplug/reconnect
testing needs a supporting adapter; Wi-Fi policy activation/reboot behavior
remains a hardware check. See docs/board-checks.md.

2026-10-21 follow-up: an explicit EEE request on the owner's unsupported
end0 driver previously aborted the entire plan after menu exit. Known driver
unsupported responses now skip only EEE with a visible note, including when
the resulting plan has no changes. Existing managed policies are left alone
without a runtime probe. Unknown status/access errors still stop apply.
Regression coverage includes combined SSH/EEE edits and unchanged-policy
planning. Real SSH terminal checks passed both an unedited exit and an
explicit unsupported Disable request in dry-run, preserving configuration
hashes/metadata and active Wi-Fi UUID. Full suite: 364 passed, 1 root-only skip.

2026-10-21 correction after owner feedback: EOPNOTSUPP from ethtool does NOT
establish lack of EEE hardware support. Real Pi 5 inspection identified macb
and BCM54213PE (PHY ID 0x600d84a2), with eee-broken-100tx and
eee-broken-1000t in both the running DT and installed base DTB. Readback now
imports these as a disabled BOOT policy for that exact PHY, and Disable
needs no extra service. The negotiated hardware state remains unmeasured;
other PHYs or partial flags are not generalized. Enable through unavailable
ethtool callbacks is still skipped with accurate wording. The earlier claim
that testing requires a different capable adapter was unjustified.
Real SSH PTY checks cover unchanged, Disable and Enable exits in dry-run,
preserving configuration/metadata and active Wi-Fi. No PHY registers or boot
files were changed. Full suite: 370 passed, 1 root-only skip.

2026-10-21 owner-authorized direct PHY inspection: read-only SIOCGMIIREG
confirmed BCM54213PE capability 0x0006, local EEE advertisement 0x0000 and
partner advertisement 0x0006, repeated with the link up. EEE is not currently
negotiated; the existing DT boot restrictions agree with hardware readback.
No writes or network restart were performed. The earlier disable command was
not found in checked startup/config files or saved history. Results and the
MDIO addressing are recorded in docs/board-checks.md. Generic policy activation
and EEE transitions remain untested; this check required no product-code change.

2026-10-21 Pi 5 EEE disable completed: the onboard Model B port now has a
persistent Device Tree overlay backend, independent of missing macb ethtool
EEE callbacks. It verifies the selected port against the installed DTB,
manages a model-filtered config.txt block, preserves vendor DTBs/IP settings,
supports Keep/Remove and backed-up recovery, and migrates old owned ethtool
rules without runtime activation. Unresolved boot layouts and foreign files
are refused; USB/other interfaces retain the generic backend. This extends
Disable, not a forced hardware Enable operation.

Physical enabled-to-disabled test passed on the owner's Pi 5: with only the
two EEE restrictions temporarily removed from a private QA copy of the base
tree, PHY advertisement was 0x0006 and the gigabit link autonegotiated with an
EEE-capable partner. Applying the exact generated production overlay restored
both restrictions and advertisement 0x0000 after reboot; its marker confirmed
the overlay actually loaded. A board-side worker and independent recovery
timer bounded the test. Repeat apply, Keep and Remove also passed on target.
Original boot DTB/config and network/SSH hashes/metadata were restored, QA
units/files removed, and native EEE disable rechecked. Backup IDs and register
observations are in docs/board-checks.md. Full suite: 397 passed, 1 root-only skip.

## Existing gaps to address alongside the stages

2026-10-21 menu polish: replaced the text-only exit question with a centered
six-row kernel-style Yes/No dialog on the blue background. Yes defaults to
review, No discards the pending session, Esc returns to editing; arrows,
Tab/Shift-Tab, Enter/Space and Y/N are supported, including terminal resizing.
Explicit No now also discards after an earlier in-session Save, rather than
continuing with that stale snapshot and potentially applying Wi-Fi drafts.
Unchanged exits still skip the prompt. Terminal tests cover navigation,
resize, cancel/default selection, save/discard and Wi-Fi draft review/apply.
Full automated suite: 360 passed, 1 root-only test skipped. No board settings
were changed for this UI update.

- Explicit distinction between leaving a feature unchanged and disabling it.
- More complete readback: firmware includes and SSH Match rules.
- Report ambiguous/unsupported settings instead of presenting guesses as facts.
- Validate target configurations before activation and preserve idempotency.
- Keep secrets out of displayed diffs and ordinary profile values.
- Wired network changes currently wait for reboot. Wi-Fi supports explicit
  checkpointed live actions; extending live activation to Ethernet needs
  the same connectivity-preserving design.

## Physical-board verification pending

- Pi 4/Pi 5: SSH target and SD-card/image workflows.
- Pi 5: GPIO UART (`uart0_console`) and debug connector.
- Users: password-hash login, group changes and offline-media boot. New-user
  SSH-key login, locked-password status and passwordless sudo passed on Pi 5.
- Wi-Fi country/radio setting, wrong credentials, hidden-AP association,
  no-Internet behavior, and recovery of SSH actually carried over Wi-Fi.
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
Remaining stage-4 work: overlays/parameters and
Bluetooth. Baud rates/additional I2C/SPI buses are not part of this milestone.

## Stage 4: GPIO 1-Wire (2026-10-19)

Implemented Keep/Enable/Disable managed overlay and BCM GPIO 2-27 (default 4)
using the target's installed w1-gpio overlay. Replaces the managed master on
GPIO changes, retains scope boundaries on disable, and reads inline or
subsequent dtparam values. Reuses a matching external master without adding
another; changing/disabling foreign configuration is refused. Unknown
includes/filters, multiple masters and unsupported variants stay at Keep
with diagnostics, and explicit changes require resolving the ambiguity.
Known I2C/SPI pin conflicts and same-profile serial-console conflicts are
checked; arbitrary HATs/overlays and electrical wiring require operator review.

Automated checks cover rootfs edits, board filters/detection, repeat apply,
dry-run, profile validation, external/ambiguous readback, missing overlay,
pin conflicts and real terminal GPIO editing. No live pin/module commands
are scheduled. Full suite: 207 passed, 1 root-only skip.
Physical controller enumeration, sensor communication and
offline-media boot remain pending: the owner currently has no test device.
No changes or reboots are made on the owner's board for this milestone.
Next functional milestone: additional UARTs.

## Stage 4: additional UARTs (2026-10-19)

Implemented per-port Keep/Enable/Disable managed overlay plus CTS/RTS for
Pi 4 UART2-5 and Pi 5 UART0-4. Model-specific installed overlays are required;
readback covers inline/colon/subsequent dtparam flow settings, reuses matching
external configuration, and reports unresolved settings rather than rewriting
them. Base-dtb controls, unverified Pi 5 overlay-map aliases, RS485/custom
parameters, multiple/conditional overlays and includes need explicit resolution.

Validated known GPIO conflicts with I2C/SPI, 1-Wire, other UARTs and the
primary console/getty. Changes to console pins and unresolved tty-controller
maps are refused; kept/unchanged controllers retain their configuration.
No runtime serial/overlay/getty commands are scheduled. UART IDs and tty
numbers are deliberately not assumed equivalent. Baud/parity/runtime flow
control remain application settings. Bluetooth configuration is preserved.

Tests exercise every supported port with/without CTS/RTS, repeat apply,
readback, disable, board detection, profile dependencies, old boot paths,
missing overlays, dry-run, live plans, GPIO/console/getty guards and terminal
menu editing. Full suite: 255 passed, 1 root-only skip.
The initial implementation left the owner's board unchanged; subsequent
Pi 5 hardware results and fixes are recorded below. External wiring, Pi 4
and offline-media boot remain unverified.
Next functional milestone: Device Tree overlays and parameters, then Bluetooth.

## Pi 5 UART verification (2026-10-19)

Tested on the owner's Pi 5/Debian 12, kernel 6.12.96+rpt-rpi-2712. Preserved
legacy UART0, the debug-console UART10 and the custom MIPI0 camera overlay.

- Added target overlay_map.dtb reading after the real configuration exposed
  rejection of generic uart0 plus dtparam=uart0=on. Accept aliases only with
  an exact bcm2712 mapping in a valid supported FDT; known base controls stay
  external and no longer invalidate an unrelated current overlay.
- Fixed Pi 5 CTS/RTS generation: ctsrts=off still adds flow-control pinctrl.
  This caused UART4 to fail binding against existing UART0 on GPIO 14/15.
  Now disablement omits the flag, readback reflects Pi 5 presence semantics,
  and old managed blocks are migrated on reapplication.
- UART1-4 appeared with the expected RP1 bindings and pinmux after reboot;
  UART1-3 had CTS/RTS while UART4 retained only TX/RX. Internal PL011 loopback
  passed 1024-byte patterns on each port at 9600, 115200 and 921600 baud.
  UART1-3 also passed internal loopback with CRTSCTS at 115200 baud.
- CTS/RTS removal released its GPIOs after reboot without losing tty devices.
  Explicit UART4 CTS/RTS was correctly refused due to existing UART0. Disable
  removed UART1-4 after reboot, preserving UART0 and UART10. Repeat apply
  was a no-op before/after reboot and for each configuration transition.
- Camera/media/I2C/SPI/Bluetooth enumeration, active services and console
  binding matched the initial snapshot. Original config.txt content/metadata
  and GPIO 0-17 state were restored exactly after the tests.

Full automated suite: 276 passed, 1 root-only skip; native dtc output is
accepted by the alias reader. External TX/RX and CTS/RTS wires, attached
devices, camera image capture, Pi 4 and offline-media boot remain untested.
Detailed results and backup IDs are in docs/board-checks.md.
