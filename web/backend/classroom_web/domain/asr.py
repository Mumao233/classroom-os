"""Shared validation for the bounded ASR adapters."""
from hashlib import sha256
import struct
from uuid import UUID, uuid5, NAMESPACE_URL


class ProtocolError(ValueError):
    pass


def integer(value, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError('invalid integer')
    return value


def uid(value):
    if type(value) is not str or len(value) != 36:
        raise ProtocolError('canonical UUID required')
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise ProtocolError('canonical UUID required') from None
    if canonical != value:
        raise ProtocolError('canonical UUID required')
    return value


def identity(*parts):
    return str(uuid5(NAMESPACE_URL, repr(parts)))


def _wave_container(pcm):
    """Recognize complete WAVE structure, never infer a container from magic.

    This bounded sniff is not a decoder or proof of the declared raw format.
    RIFF/RIFX chunk sizes and RF64 ds64 lengths must describe all supplied bytes.
    """
    magic = pcm[:4]
    if len(pcm) < 44 or magic not in (b'RIFF', b'RIFX', b'RF64') or pcm[8:12] != b'WAVE':
        return False
    order = '>' if magic == b'RIFX' else '<'
    def number(offset, size=4):
        return int.from_bytes(pcm[offset:offset + size], 'big' if order == '>' else 'little')
    position, extended, data_size, sample_count = 12, {}, None, None
    if magic == b'RF64':
        if number(4) != 0xffffffff or pcm[12:16] != b'ds64':
            return False
        size = number(16)
        if size < 28 or 20 + size > len(pcm) or size != 28 + 12 * number(44):
            return False
        if number(20, 8) != len(pcm) - 8:
            return False
        data_size, sample_count = number(28, 8), number(36, 8)
        for offset in range(48, 20 + size, 12):
            extended.setdefault(pcm[offset:offset + 4], []).append(number(offset + 4, 8))
        position = 20 + size
    elif number(4) != len(pcm) - 8:
        return False
    fmt, data = None, None
    while position < len(pcm):
        if position + 8 > len(pcm):
            return False
        kind, size = pcm[position:position + 4], number(position + 4)
        if size == 0xffffffff:
            if magic != b'RF64':
                return False
            if kind == b'data' and data is None:
                size = data_size
            elif extended.get(kind):
                size = extended[kind].pop(0)
            else:
                return False
        start = position + 8
        position = start + size + size % 2
        if position > len(pcm):
            return False
        if kind == b'fmt ':
            if fmt is not None or size < 16 or (size != 16 and (size < 18 or number(start + 16, 2) != size - 18)):
                return False
            fmt = struct.unpack(order + 'HHIIHH', pcm[start:start + 16])
            tag, channels, rate, byte_rate, align, bits = fmt
            if not all((tag, channels, rate, byte_rate, align)):
                return False
            if tag == 1 and (not bits or bits % 8 or align != channels * (bits // 8) or byte_rate != rate * align):
                return False
        elif kind == b'data':
            if fmt is None or data is not None or (data_size is not None and size != data_size):
                return False
            data = size
    if fmt is None or data is None or any(extended.values()):
        return False
    if fmt[0] == 1 and (data % fmt[4] or (sample_count not in (None, 0, data // fmt[4]))):
        return False
    return True


def validate_frame(frame, *, max_bytes):
    keys = {'pcm', 'sample_rate', 'channels', 'encoding', 'start_sample', 'end_sample',
            'sample_count', 'byte_length', 'sha256'}
    if type(frame) is not dict or set(frame) != keys:
        raise ProtocolError('invalid frame descriptor')
    pcm = frame['pcm']
    if type(pcm) is not bytes or not pcm or len(pcm) > max_bytes or len(pcm) % 2:
        raise ProtocolError('PCM length bound')
    if _wave_container(pcm):
        raise ProtocolError('WAV container is not raw PCM')
    if (integer(frame['sample_rate']), integer(frame['channels']), frame['encoding']) != (16000, 1, 'signed16le'):
        raise ProtocolError('PCM format required')
    start, end = integer(frame['start_sample']), integer(frame['end_sample'])
    count = integer(frame['sample_count'], 1)
    if end - start != count or integer(frame['byte_length']) != count * 2 or len(pcm) != count * 2:
        raise ProtocolError('range/length mismatch')
    if frame['sha256'] != sha256(pcm).hexdigest():
        raise ProtocolError('hash mismatch')
    return pcm, start, end
