"""
Linux Kernel TAP Interface Bridge (/dev/net/tun).
Engineered with zero third-party dependencies using native stdlib fcntl & os.
"""

import os
import fcntl
import struct
from typing import Optional

# Linux Kernel TUN/TAP constants (from linux/if_tun.h)
TUNSETIFF = 0x400454CA
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000


class LinuxTapBridge:
    """Manages raw Layer-2 Ethernet TAP device via Linux /dev/net/tun."""

    def __init__(self, dev_name: str = "tap-radio", mtu: int = 180):
        self.dev_name = dev_name
        self.mtu = mtu
        self.fd: Optional[int] = None
        self._open_tap()

    def _open_tap(self):
        try:
            self.fd = os.open("/dev/net/tun", os.O_RDWR)
        except PermissionError:
            raise PermissionError(f"Root/CAP_NET_ADMIN privileges required to access /dev/net/tun for {self.dev_name}")

        # Interface request structure (struct ifreq)
        ifr = struct.pack("16sH", self.dev_name.encode("ascii"), IFF_TAP | IFF_NO_PI)
        fcntl.ioctl(self.fd, TUNSETIFF, ifr)

        # Set non-blocking I/O
        flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        print(f"[TAP] Attached to native Linux TAP device: {self.dev_name} (MTU: {self.mtu})")

    def read(self, bufsize: int = 2048) -> bytes:
        """Read a raw Layer-2 Ethernet frame from the kernel."""
        if self.fd is None:
            raise RuntimeError("TAP device is not open")
        try:
            return os.read(self.fd, bufsize)
        except BlockingIOError:
            return b""

    def write(self, frame: bytes) -> int:
        """Inject a raw Layer-2 Ethernet frame into the kernel."""
        if self.fd is None:
            raise RuntimeError("TAP device is not open")
        return os.write(self.fd, frame)

    def get_mac_address(self) -> Optional[bytes]:
        """Reads local interface MAC address from /sys/class/net/."""
        path = f"/sys/class/net/{self.dev_name}/address"
        try:
            with open(path, "r") as f:
                mac_str = f.read().strip()
                return bytes.fromhex(mac_str.replace(":", ""))
        except Exception:
            return None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            print(f"[TAP] Closed TAP device: {self.dev_name}")
