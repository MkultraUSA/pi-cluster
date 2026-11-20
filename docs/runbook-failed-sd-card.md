# Runbook: a Pi's SD card is failing

Written from a real recovery on 2026-11-19. The node was a Pi 5 running
radio decoding and logging software. It had already worn out one card and was
failing a second, because the application wrote constantly to the card.

## Symptoms

- SSH connects, then times out at the banner, or the host drops off the LAN.
- A reboot does not bring it back.

## Procedure

1. **Image the card before touching it.** Put it in a USB reader on a
   workstation and read the raw device to a file. Use small retries per
   4 KB sector and log any unreadable sector instead of stopping. On this
   card nothing was unreadable. Record the SHA-256 of the image.
2. **Back up at the file level too.** Archive configs and application data
   from the image or the live host, and keep a second copy offsite
   (encrypted before it leaves the LAN).
3. **Write the image to a new card** and read it back. Compare the read-back
   hash to the image hash. Do not trust a write that was not verified.
4. **Boot the new card.** Grow the root partition to fill it (save the old
   partition table first).
5. **Take the writes off the card.** Move the write-heavy directories (logs,
   databases, call recordings) to an SSD and bind-mount them into their
   original paths in `/etc/fstab`:
   - `nofail` so a missing SSD does not block boot
   - `x-systemd.requires-mounts-for=` so services start after the mount
   - leave the old copies on the card as a fallback
6. **Pin the address.** Give the wired interface a static IP in netplan so
   the node keeps the same address after recovery.
7. **Verify.** Reboot, confirm every mount comes back, confirm the services
   run, and confirm their output is arriving where it should (here, audio
   clips reaching a remote server).

## What I learned

- **Not every USB-SATA bridge is safe for a busy disk.** One ASMedia
  adapter dropped off the USB bus under mixed small writes and heat. It was
  fine for plain sequential copies, which is why it passed my first test.
  A Samsung T7 handled the same workload without problems.
- **Rule out power before blaming the disk.** `vcgencmd get_throttled`
  returned `0x0`, so under-voltage was not the cause.
- **Plan for the card to be disposable.** The OS lives on cheap media that is
  easy to replace, and anything that matters lives on the SSD and in backups.
