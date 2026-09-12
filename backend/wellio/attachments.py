import hashlib
import io
import json
import os
import re
import shutil
import stat
import threading
import warnings
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image

from .errors import BackendError
from .validation import require_id, require_int, strict_object

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_PIXELS = 40_000_000


def validate_image(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        expected = "PNG"
    elif data.startswith(b"\xff\xd8\xff"):
        expected = "JPEG"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        expected = "WEBP"
    else:
        raise BackendError("UNSUPPORTED_ATTACHMENT_FORMAT", 415)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected or image.width * image.height > MAX_ATTACHMENT_PIXELS:
                    raise ValueError("invalid image dimensions")
                image.verify()
            # Decode every frame, not merely headers. Bound cumulative animation size.
            with Image.open(io.BytesIO(data)) as image:
                pixels = 0
                for index in range(getattr(image, "n_frames", 1)):
                    image.seek(index)
                    pixels += image.width * image.height
                    if pixels > MAX_ATTACHMENT_PIXELS:
                        raise ValueError("image too large")
                    image.load()
    except Exception:
        raise BackendError("INVALID_ATTACHMENT_CONTENT", 400) from None
    return "image/" + expected.lower()


class AttachmentStore:
    def __init__(self, base_path):
        self.root = Path(base_path).absolute()
        self._lock = threading.RLock()

    @staticmethod
    def _directory(path, create=False, parents=False):
        if create:
            path.mkdir(mode=0o700, parents=parents, exist_ok=True)
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise BackendError("ATTACHMENT_STORAGE_INVALID", 500)
        if create:
            path.chmod(0o700)
        elif stat.S_IMODE(mode) != 0o700:
            raise BackendError("ATTACHMENT_STORAGE_INVALID", 500)

    @staticmethod
    def _write(path, data):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())

    @staticmethod
    def _read(path, maximum):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > maximum:
                raise BackendError("ATTACHMENT_STORAGE_INVALID", 500)
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise BackendError("ATTACHMENT_STORAGE_INVALID", 500)
            return data

    def upload(self, session_id, epoch, data, name, purpose, media_type=None):
        require_id(session_id); require_int(epoch)
        if purpose not in ("food", "menu"):
            raise BackendError("INVALID_INPUT", 400)
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise BackendError("ATTACHMENT_TOO_LARGE", 413)
        if not data:
            raise BackendError("INVALID_ATTACHMENT_CONTENT", 400)
        actual_type = validate_image(data)
        # Match the previous contract: signature and actual decoding are authoritative;
        # browser-supplied MIME is not trusted as proof of the image format.
        with self._lock:
            attachment_id = str(uuid4())
            clean_name = re.sub(r"[\x00-\x1f\x7f]", "", (name or "").replace("\\", "/").split("/")[-1]).strip()[:200]
            attachment = {"id": attachment_id, "url": f"/api/attachments/{attachment_id}", "name": clean_name or "image." + actual_type[6:], "mediaType": actual_type, "purpose": purpose}
            session_path = self.root / session_id
            epoch_path = session_path / str(epoch)
            self._directory(self.root, True, True)
            self._directory(session_path, True)
            self._directory(epoch_path, True)
            staging = epoch_path / f".pending-{attachment_id}"
            self._directory(staging, True)
            try:
                self._write(staging / "image.bin", data)
                metadata = {"sessionId": session_id, "resetEpoch": epoch, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "attachment": attachment}
                self._write(staging / "metadata.json", json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode())
                staging.rename(epoch_path / attachment_id)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            return attachment

    def read(self, session_id, epoch, attachment_id):
        require_id(session_id); require_int(epoch)
        try:
            if str(UUID(attachment_id)) != attachment_id.lower():
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise BackendError("INVALID_INPUT", 400) from None
        with self._lock:
            try:
                session_path = self.root / session_id
                epoch_path = session_path / str(epoch)
                directory = epoch_path / attachment_id
                for path in (self.root, session_path, epoch_path, directory):
                    self._directory(path)
                metadata = json.loads(self._read(directory / "metadata.json", 16 * 1024))
                strict_object(metadata, {"sessionId", "resetEpoch", "size", "sha256", "attachment"})
                require_int(metadata["resetEpoch"])
                attachment = strict_object(metadata["attachment"], {"id", "url", "name", "mediaType", "purpose"})
                if metadata["sessionId"] != session_id or metadata["resetEpoch"] != epoch or attachment["id"] != attachment_id or attachment["url"] != f"/api/attachments/{attachment_id}":
                    raise BackendError("ATTACHMENT_NOT_FOUND", 404)
                if attachment["mediaType"] not in ("image/png", "image/jpeg", "image/webp") or attachment["purpose"] not in ("food", "menu") or not isinstance(attachment["name"], str) or not 1 <= len(attachment["name"]) <= 200:
                    raise BackendError("ATTACHMENT_STORAGE_INVALID", 500)
                require_int(metadata["size"], max=MAX_ATTACHMENT_BYTES)
                data = self._read(directory / "image.bin", MAX_ATTACHMENT_BYTES)
                if len(data) != metadata["size"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                    raise BackendError("ATTACHMENT_STORAGE_INVALID", 500)
                return {"attachment": attachment, "bytes": data, "mediaType": attachment["mediaType"]}
            except FileNotFoundError:
                raise BackendError("ATTACHMENT_NOT_FOUND", 404) from None
            except BackendError as error:
                if error.code == "INVALID_INPUT":
                    raise BackendError("ATTACHMENT_STORAGE_INVALID", 500) from None
                raise
            except (OSError, ValueError, TypeError, KeyError):
                raise BackendError("ATTACHMENT_STORAGE_INVALID", 500) from None

    def reset(self, session_id, through_epoch=None):
        require_id(session_id)
        if through_epoch is not None:
            require_int(through_epoch)
        with self._lock:
            session_path = self.root / session_id
            try:
                self._directory(self.root)
                self._directory(session_path)
            except FileNotFoundError:
                return
            if through_epoch is None:
                shutil.rmtree(session_path)
            else:
                for directory in session_path.iterdir():
                    if re.fullmatch(r"[1-9]\d*", directory.name) and int(directory.name) <= through_epoch:
                        if directory.is_symlink():
                            directory.unlink()
                        elif directory.is_dir():
                            shutil.rmtree(directory)
