"""기존 코드 기반 십자가 로고의 PNG 출력. 외부 패키지·인증키 불필요.

icon.svg와 같은 512 단위 도형을 3x supersampling으로 렌더링합니다.
"""
import struct
import zlib
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "static" / "app-icons"
NAVY, RED, WHITE = (23, 55, 94), (229, 72, 77), (255, 255, 255)


def color_at(x, y):
    if (230 <= x < 282 and 166 <= y < 346) or (166 <= x < 346 and 230 <= y < 282):
        return WHITE
    if 116 <= x < 396 and 116 <= y < 396:
        cx, cy = min(max(x, 192), 320), min(max(y, 192), 320)
        if (x - cx) ** 2 + (y - cy) ** 2 <= 76 ** 2:
            return RED
    return NAVY


def png(size):
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            samples = [color_at((x + (sx + .5) / 3) * 512 / size,
                                (y + (sy + .5) / 3) * 512 / size)
                       for sy in range(3) for sx in range(3)]
            raw.extend(round(sum(c[channel] for c in samples) / 9) for channel in range(3))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        (OUTPUT / f"icon-{size}.png").write_bytes(png(size))
    # 로고가 중심 80% 안전 원 안에 있어 maskable에도 사용할 수 있습니다.
    (OUTPUT / "maskable-512.png").write_bytes((OUTPUT / "icon-512.png").read_bytes())
