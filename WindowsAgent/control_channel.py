"""Bounded WebSocket messages for an authenticated, ordered control channel."""
from __future__ import annotations
import base64
import hashlib
import json
import struct


class ChannelClosed(Exception):
    pass


class WebSocket:
    MAX_MESSAGE = 65536

    def __init__(self, reader, writer) -> None:
        self.reader, self.writer = reader, writer

    @staticmethod
    def accept_key(key: str, version: str) -> str:
        try:
            decoded = base64.b64decode(key, validate=True)
        except (ValueError, TypeError):
            decoded = b""
        if version != "13" or len(decoded) != 16:
            raise ValueError("WebSocket handshake invalid")
        return base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")

    def _read(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.reader.read(size - len(chunks))
            if not chunk:
                raise ChannelClosed()
            chunks.extend(chunk)
        return bytes(chunks)

    def _send(self, opcode: int, payload: bytes) -> None:
        size = len(payload)
        header = bytes((0x80 | opcode, size)) if size < 126 else (
            bytes((0x80 | opcode, 126)) + struct.pack("!H", size) if size <= 65535 else
            bytes((0x80 | opcode, 127)) + struct.pack("!Q", size))
        self.writer.write(header + payload)
        self.writer.flush()

    def close(self, code: int = 1000) -> None:
        self._send(8, struct.pack("!H", code))

    def send(self, data: dict) -> None:
        self._send(1, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def receive(self) -> dict:
        fragments = bytearray()
        fragmented = False
        while True:
            first, second = self._read(2)
            opcode, size = first & 15, second & 127
            final = bool(first & 0x80)
            if first & 0x70 or not second & 0x80 or opcode not in (0, 1, 8, 9, 10):
                self.close(1002)
                raise ChannelClosed()
            if opcode >= 8 and (size > 125 or not final or (opcode == 8 and size == 1)):
                self.close(1002)
                raise ChannelClosed()
            if (opcode == 0 and not fragmented) or (opcode == 1 and fragmented):
                self.close(1002)
                raise ChannelClosed()
            if size == 126:
                size = struct.unpack("!H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read(8))[0]
            if size + len(fragments) > self.MAX_MESSAGE:
                self.close(1009)
                raise ChannelClosed()
            mask = self._read(4)
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(self._read(size)))
            if opcode == 8:
                self.close()
                raise ChannelClosed()
            if opcode == 9:
                self._send(10, payload)
                continue
            if opcode == 10:
                continue
            fragments.extend(payload)
            if not final:
                fragmented = True
                continue
            try:
                message = json.loads(fragments.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError()
                return message
            except (ValueError, UnicodeDecodeError):
                self.close(1007)
                raise ChannelClosed() from None
