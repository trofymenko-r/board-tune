# Wi-Fi manager proposal

Status: draft for owner review, 2026-10-19. This document proposes the
interaction and implementation boundaries; runtime behavior is unchanged.

## Observed problem

The existing Wi-Fi hook imports one representable NetworkManager profile,
preferring wlan0, higher autoconnect priority, then filename. It does not
query the currently active connection. A stored profile therefore appears
as though it describes the current connection, and the other profiles
are inaccessible from the menu.

Read-only inspection of the Pi 5 at mipi@192.168.0.11 found three saved
Wi-Fi profiles: wireless90-5G, Sweet Home, and preconfigured (SSID SweetHome).
The active profile/SSID was wireless90-5G on wlan0, IPv4 192.168.1.156,
with reported signal 55% on 5 GHz. The SSH address 192.168.0.11 belongs to
Ethernet end0. No connection changes or forced scans were performed.
The cached access-point list contained only the active access point; it
does not establish how many other networks are actually nearby.

## Entry and navigation

Replace the interactive single-profile entry with `Wi-Fi --->`. Open a
dedicated manager in the same centered gray dialog, blue background,
keyboard controls, and button style as the rest of menuconfig. Keep the
existing WIFI_* profile symbols and batch workflow compatible.

Illustrative home screen, using the observed board state:

```text
+------------------------- Wi-Fi -------------------------+
| Interface: wlan0                                        |
| Connected: wireless90-5G                                 |
| IPv4: 192.168.1.156    Signal: 55%    Band: 5 GHz         |
|                                                         |
| > Saved networks (3)                                --->|
|   Available networks                               --->|
|   Add network / hidden SSID                         --->|
|   Radio and country                                --->|
|                                                         |
|                < Refresh >  < Back >                    |
+---------------------------------------------------------+
```

For multiple adapters, interface selection precedes network actions. Active
runtime data is labeled separately from saved settings and pending edits.
Opening the manager or inspecting a profile never changes configuration.

## Saved networks

List every profile applicable to the selected adapter. Show SSID, active
status, availability from the latest scan, autoconnect and priority. The
profile name is shown separately when it differs from the SSID. Profiles
bound to other adapters remain discoverable through an all-profiles view.

Identify profiles by UUID, not by SSID or display name. Multiple profiles
for the same SSID remain separate and show identifying details. Order the
active profile first, then nearby saved networks, then the remaining
profiles. Never choose the current network by autoconnect priority.

Enter opens details and actions: Connect, Edit, Forget, Back. Details show
saved security/address settings and, for the active connection, actual
addresses, gateway, DNS, BSSID and channel. Forget requires confirmation;
forgetting an active profile explains and handles the disconnection.

Editing a selected existing profile preserves its UUID and unrelated
settings. Saving must not create an additional board-tune profile for the
same selected connection. Unsupported profiles are still listed and
identified; unsupported edits are disabled with an explanation.

## Available networks

Use access points seen by the board's selected adapter, not by the host
computer. Show SSID, signal percentage, security, band(s), saved status,
and connected status. Group access points with the same SSID and security
into one selectable network; show individual BSSIDs/channels in details.
Do not silently lock a saved connection to one BSSID.

On entry, request a fresh scan with a bounded wait. Show progress, permit
cancellation, and offer a Rescan action. Label cached results as cached
when the scan fails or has not completed. Radio disabled, hardware block,
missing adapter, scan error, and an empty successful scan have distinct
messages. Live radio state must never be inferred from saved profiles.

## Connecting and credentials

Selecting a known network reuses a selected saved profile and stored key.
If multiple profiles match, offer a profile choice. An unknown network
opens a short form: SSID, detected security, masked password input, and
Connect now / Save only / Cancel. Hidden networks use the same form with
manual SSID/security selection. Existing credentials are never displayed.

Initially support creating open, WPA2/WPA3 Personal and WPA3-only profiles,
as the existing hook does. Enterprise/WEP networks are visible but creating
their credentials is outside the first increment. Existing complex
profiles may be activated through NetworkManager without rewriting them;
if extra credentials are needed, report that limitation clearly.

Password input stays out of argv, application logs, diffs, and ordinary
exported profiles. Interactive entry does not require a user-created
password file; the existing password-file option remains for batch use.
Private temporary transport files, if needed, have restricted permissions
and are removed after use. Saved credentials use NetworkManager's
protected profile storage.

Proposed action semantics, pending owner agreement:

- Save only stages profile changes for the normal review/apply step;
  it never disconnects or activates a network.
- Connect now explicitly reviews the selected profile changes and then
  performs that Wi-Fi action immediately, without reboot. The dialog
  distinguishes this from pending menu changes. Existing unrelated edits
  are not applied. Changes already confirmed live are not discarded by
  later exiting menuconfig without saving other drafts.
- Connecting shows progress, then the actual resulting connection and IP
  address. Failure shows an actionable NetworkManager reason. Association,
  address assignment and Internet connectivity are distinct statuses;
  a network without Internet can still be accepted.

## Switching over SSH

Before a live change, show whether this SSH session uses the affected
adapter. Create a NetworkManager checkpoint scoped to that adapter with
an automatic rollback deadline (proposed: 60 seconds). The countdown covers
activation as well as confirmation and is visible throughout. The board
owns the timeout, so it does not depend on the SSH client staying alive.
On success ask Keep connection / Revert; do not require Internet access
as the success criterion. Without confirmation, request/await rollback
and refresh the actual device state. Checkpoint restoration can fail if
the old access point disappears, so it is not an unconditional guarantee.

If switching the Wi-Fi carrying SSH breaks the session, automatically
retry the known SSH destination until the rollback deadline. First-version
behavior relies on rollback to recover that address; finding a newly
assigned address on a different network is not promised. Ethernet SSH,
as currently available on the test board, permits a confirmed switch even
when the Wi-Fi address changes.

If checkpoint support is unavailable, expose the limitation and allow
Save only; do not perform an unguarded switch over the affected SSH link.
Back up persistent files independently of the runtime checkpoint. Test
rollback of new profiles/changed credentials as well as device activation;
an unsuccessful trial must not leave a new preferred autoconnect profile.
Do not disable or reconfigure Ethernet as part of a Wi-Fi operation.

NetworkManager documents automatic timeout rollback via
[CheckpointCreate](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.html#org.freedesktop.NetworkManager.CheckpointCreate)
and remote-change workflows via
[nmcli device checkpoint](https://networkmanager.dev/docs/api/latest/nmcli.html).

## Offline targets and batch compatibility

For an SD card/image, show and edit saved profiles, add/forget profiles,
and configure autoconnect/priority. Runtime connection status, scanning
and Connect now are unavailable with a short explanation. Profile changes
apply on next boot. Show configured country separately from the active
regulatory domain; retain the existing reboot requirement for changing
the country in this increment.

Keep existing CLI profiles valid. Put NetworkManager inventory, parsing,
profile selection and live connection transactions in a separate service
layer shared by readback and the UI. Pass the already-open target into
the manager. Add a scoped action callback in the menu adapter rather than
encoding changing AP lists as static Kconfig choices. Refresh runtime
state after any operation; retain unsaved edits belonging to other hooks.

## Proposed implementation sequence

1. Correct active-profile readback and add the status/saved-profile browser,
   including offline behavior and stable UUID identity. Commit and verify
   on the board without activating networks.
2. Add AP scanning, saved-profile editing and the masked credential form,
   preserving unknown fields and preventing duplicate profiles. Commit
   after parser, profile roundtrip and terminal interaction checks.
3. Add Connect now, confirmation, bounded waits, board-side rollback and
   SSH recovery. Test failure paths with fake transport first. Commit
   after reviewable results; arrange a controlled board check using a
   network the owner permits for activation, keeping Ethernet access.

Hardware verification must cover wrong credentials, unavailable AP,
successful saved/new connections, absence of Internet, cancellation,
timeout and SSH loss. Do not mark these passed based on fake transports.
