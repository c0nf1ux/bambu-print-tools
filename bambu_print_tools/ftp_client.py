"""Send files to a Bambu printer over LAN implicit FTPS, bypassing the
slicer GUI entirely. Ported from the a1-mini-painting-station project's
scripts/send_to_printer.py (2026-09-10), generalized to take a config dict
instead of a hardcoded project-local file.

No MCP server, no background process - one short-lived connection per call.
"""
import ftplib
import ssl
from pathlib import Path


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """ftplib.FTP_TLS only speaks explicit FTPS (AUTH TLS on port 21).
    Bambu printers use implicit FTPS (TLS handshake immediately on connect,
    port 990) - this subclass wraps the socket in TLS as soon as it's set,
    the standard workaround for implicit-mode FTPS in Python."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value


def connect(config: dict) -> ImplicitFTP_TLS:
    # Bambu printers serve a self-signed cert on their local FTPS server (no
    # real CA - this is how the official slicers connect locally too). The
    # Access Code is the actual auth boundary, not cert trust. This is a
    # narrow exception for this one LAN device class - don't copy the
    # cert-verification skip into code talking to anything else.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    ftp = ImplicitFTP_TLS(context=ctx)
    ftp.connect(host=config["ip"], port=config.get("ftp_port", 990), timeout=15)
    ftp.login(user="bblp", passwd=config["access_code"])
    ftp.prot_p()
    return ftp


def list_files(ftp: ImplicitFTP_TLS) -> list[str]:
    lines: list[str] = []
    ftp.retrlines("LIST", lines.append)
    return lines


def upload(ftp: ImplicitFTP_TLS, local_path: Path) -> None:
    # This printer's embedded FTP server doesn't complete the TLS
    # close_notify handshake cleanly after a large transfer, so ftplib's
    # post-transfer conn.unwrap() times out even though the file itself
    # already landed correctly (confirmed byte-for-byte on the printer).
    # Worse: the control connection is left desynced afterward (a later
    # PASV can come back malformed) - don't reuse `ftp` for anything after
    # this call. Verify on a fresh connection instead.
    try:
        with open(local_path, "rb") as f:
            ftp.storbinary(f"STOR {local_path.name}", f)
    except (TimeoutError, ssl.SSLError):
        pass


def remote_size(ftp: ImplicitFTP_TLS, filename: str) -> int | None:
    """ftp.size() doesn't work reliably on this server (returns None) -
    parse LIST output instead, which is confirmed reliable."""
    for line in list_files(ftp):
        parts = line.split()
        if parts and parts[-1] == filename:
            return int(parts[4])
    return None


def verify_upload(config: dict, local_path: Path) -> None:
    """Open a brand-new connection to check the file landed correctly - must
    be fresh, not the one upload() just used (desynced by the quirk above)."""
    ftp = connect(config)
    local_sz = local_path.stat().st_size
    remote_sz = remote_size(ftp, local_path.name)
    ftp.quit()

    if remote_sz != local_sz:
        raise RuntimeError(
            f"Upload verification failed: printer has {remote_sz} bytes, "
            f"local file is {local_sz} bytes"
        )


def send(config: dict, local_path: Path) -> None:
    """Upload + verify in one call, the common case."""
    ftp = connect(config)
    upload(ftp, local_path)
    try:
        ftp.close()  # not quit() - control channel is desynced post-upload
    except Exception:
        pass
    verify_upload(config, local_path)
