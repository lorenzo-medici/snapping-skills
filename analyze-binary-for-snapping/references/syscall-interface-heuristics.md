# Syscall/Resource Evidence → Snap Interface Heuristics

Use this mapping with runtime trace evidence (`strace`) and static path hints.
Treat runtime evidence as stronger than static strings.

---

## Confidence Model

- **High:** Direct observed syscall/path strongly tied to one interface.
- **Medium:** Inference from static paths or ambiguous runtime evidence.
- **Low:** Weak/non-specific evidence; keep as “candidate only”.

---

## Network

| Evidence | Suggested plug | Confidence | Notes |
|---|---|---|---|
| `connect()` to remote IP/host | `network` | high | Outbound client networking |
| `bind()` on non-privileged port (>=1024) | `network-bind` | medium | Often required for listeners |
| `bind()` on privileged port (<1024) | `network-bind` | high | Classic privileged bind case |
| Raw sockets (`socket(AF_PACKET, ...)`) | `network-control` | high | Usually requires store review |
| `ioctl`/ops on `/dev/net/tun` | `network-control` | high | TUN/TAP management |

---

## Devices / Hardware

| Evidence | Suggested plug | Confidence | Notes |
|---|---|---|---|
| Access `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*` | `serial-port` | high | Serial hardware |
| Access `/dev/bus/usb/*` | `raw-usb` | high | Raw USB access |
| Access `/dev/video*` | `camera` | high | Camera devices |
| Access `/dev/i2c-*` | `i2c` | high | I2C bus |
| Access `/dev/spidev*` | `spi` | high | SPI bus |
| Access `/dev/gpio*` or `/sys/class/gpio/*` | `gpio` | high | GPIO control |
| Access `/dev/dri/*` | `opengl` | medium | Graphics/GPU stack varies |
| Access input devices (`/dev/input/*`) | `raw-input` or `joystick` | medium | Choose based on actual use |

---

## System Observation / Process Control

| Evidence | Suggested plug | Confidence | Notes |
|---|---|---|---|
| Read `/proc/*` and process metadata beyond own process | `system-observe` | medium | Often needed for monitoring agents |
| `ptrace(...)` usage | `process-control` | high | Cross-process debugging/control |
| Priority/scheduler changes impacting other processes | `process-control` | medium | e.g., `setpriority` on external processes |
| Clock/time setting operations | `time-control` | high | System time modifications |
| Raw I/O style access | `raw-io` | high | Usually store-review-sensitive |

---

## Filesystem / Home

| Evidence | Suggested plug | Confidence | Notes |
|---|---|---|---|
| Access under `/home/<user>/...` | `home` | high | Needed for user files in home |
| Access removable media mount points | `removable-media` | high | `/media`, `/mnt` style paths |
| Writes under system dirs (`/etc`, `/var/lib`, `/opt`) | layout/env override first | high | Prefer layout/redirect over broad plugs |

---

## Common “Do Not Auto-Add” Cases

| Evidence | Action | Reason |
|---|---|---|
| Generic `CAP_AUDIT_WRITE` in OCI defaults | drop (unless explicit app need) | Common container default, not usually required in snaps |
| `CAP_SYS_MODULE`, `CAP_SYS_BOOT`, `CAP_AUDIT_CONTROL`, `CAP_AUDIT_READ` | flag manual review | Not generally grantable in strict confinement |
| `mount`, `pivot_root`, namespace-management syscalls | flag incompatibility/manual redesign | Often incompatible with strict confinement |

---

## Decision Procedure

1. Start from observed runtime evidence.
2. Add high-confidence plugs first.
3. Add medium-confidence plugs only if corroborated by multiple signals.
4. Keep low-confidence items in notes, not auto-applied.
5. Explicitly flag any store-review-only or non-grantable requirements.

