import base64
import getpass
import logging
import re
import socket
import subprocess
import traceback
from datetime import datetime, timezone

_SCREENSHOT_PS1 = r"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing

$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$bmp = New-Object System.Drawing.Bitmap($screen.Bounds.Width, $screen.Bounds.Height)
$gfx = [System.Drawing.Graphics]::FromImage($bmp)

try {
    $gfx.CopyFromScreen($screen.Bounds.Location, [System.Drawing.Point]::Empty, $screen.Bounds.Size)

    $targetBytes = 1MB
    $quality = 90
    $minQuality = 30
    $qualityStep = 10
    $scaleStep = 0.9

    $encoder = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
               Where-Object { $_.MimeType -eq "image/jpeg" }
    $encParams = New-Object System.Drawing.Imaging.EncoderParameters(1)

    $tempPath = [IO.Path]::GetTempFileName()
    Remove-Item $tempPath
    $tempPath = "$tempPath.jpg"

    $currentImage = $bmp

    while ($true) {
        $encParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
            [System.Drawing.Imaging.Encoder]::Quality, [int]$quality)

        $currentImage.Save($tempPath, $encoder, $encParams)
        $filesize = (Get-Item $tempPath).Length

        if ($filesize -le $targetBytes -or $quality -le $minQuality) {
            break
        }

        $quality -= $qualityStep
        if ($quality -lt $minQuality) { $quality = $minQuality }
    }

    if ((Get-Item $tempPath).Length -gt $targetBytes) {
        while ((Get-Item $tempPath).Length -gt $targetBytes) {
            $newWidth = [int]($currentImage.Width * $scaleStep)
            $newHeight = [int]($currentImage.Height * $scaleStep)
            $resized = New-Object System.Drawing.Bitmap $newWidth, $newHeight
            $g = [System.Drawing.Graphics]::FromImage($resized)
            $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $g.DrawImage($currentImage, 0,0, $newWidth, $newHeight)
            $g.Dispose()
            if ($currentImage -ne $bmp) { $currentImage.Dispose() }
            $currentImage = $resized

            $encParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
                [System.Drawing.Imaging.Encoder]::Quality, [int]$quality)
            $currentImage.Save($tempPath, $encoder, $encParams)

            if ($newWidth -lt 400 -or $newHeight -lt 200) { break }
        }
    }

    $timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
    $desktop = [Environment]::GetFolderPath("Desktop")
    $outPath = Join-Path $desktop "screenshot_$timestamp.jpg"
    Move-Item -Force $tempPath $outPath
    Write-Output "Saved: $outPath"
}
finally {
    $gfx.Dispose()
    $bmp.Dispose()
    if ($currentImage -and $currentImage -ne $bmp) { $currentImage.Dispose() }
}
"""


def _get_host_info() -> tuple[str, str, str]:
    hostname = socket.gethostname()
    try:
        host_ip = socket.gethostbyname(hostname)
    except OSError:
        host_ip = "unknown"
    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"
    return username, hostname, host_ip


_HOST_USERNAME, _HOSTNAME, _HOST_IP = _get_host_info()

_screenshot_enabled: bool = True


def configure_screenshot(enabled: bool) -> None:
    global _screenshot_enabled
    _screenshot_enabled = enabled


def _capture_screenshot() -> str | None:
    if not _screenshot_enabled:
        return None
    try:
        encoded_cmd = base64.b64encode(_SCREENSHOT_PS1.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            ["powershell.exe", "-NonInteractive", "-EncodedCommand", encoded_cmd],
            capture_output=True, text=True, timeout=30,
        )
        match = re.search(r"Saved:\s+(.+?\.jpg)", result.stdout)
        if not match:
            return None

        wsl_path = subprocess.run(
            ["wslpath", "-u", match.group(1).strip()],
            capture_output=True, text=True, timeout=5,
        )
        if wsl_path.returncode != 0:
            return None

        img_bytes = open(wsl_path.stdout.strip(), "rb").read()
        return base64.b64encode(img_bytes).decode("ascii")
    except Exception:
        return None


def build_log_entry_dict(record: logging.LogRecord) -> dict:
    is_exception = record.exc_info is not None and record.exc_info[0] is not None

    log_message = record.getMessage()
    if is_exception and record.exc_info:
        tb = "".join(traceback.format_exception(*record.exc_info)).strip()
        log_message = f"{log_message}\n{tb}"

    return {
        "log_timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "log_level": record.levelname,
        "is_exception": is_exception,
        "log_message": log_message,
        "service": record.name,
        "username": _HOST_USERNAME,
        "hostname": _HOSTNAME,
        "ipv4": _HOST_IP,
        "service_path": record.pathname,
        "log_img": _capture_screenshot(),
    }


class PlainTextFormatter(logging.Formatter):
    """Human-readable single-line formatter for .log files."""

    def format(self, record: logging.LogRecord) -> str:
        entry = build_log_entry_dict(record)
        line = (
            f"{entry['log_timestamp']} [{entry['log_level']}]"
            f" {entry['service']} {entry['hostname']}({entry['ipv4']}) {entry['username']}"
            f" | {entry['log_message']}"
        )
        return line
