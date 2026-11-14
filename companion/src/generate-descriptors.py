#!/usr/bin/env python3
"""Generate byte arrays from the pinned, unmodified upstream firmware sources."""
import re
from pathlib import Path
root = Path(__file__).resolve().parents[1]
items = [('Descriptors.c', 'Report', 'hid_report'), ('emu.c', 'bufa3', 'feature_a3'),
         ('emu.c', 'buf02', 'feature_02'), ('emu.c', 'report', 'input_report')]
parts = ['/* Generated from GIMX-firmwares ab2215ebad5d50fd25b6b40ac74254f1f4c0bda5.\n'
         ' * GPL-3.0; upstream notices retained in vendor/EMUPS4PAIRING. */\n']
for filename, old, new in items:
    text = (root / 'vendor/EMUPS4PAIRING' / filename).read_text()
    body = re.search(r'\b' + old + r'\[\]\s*=\s*\{(.*?)\};', text, re.S).group(1)
    body = re.sub(r'/\*.*?\*/|//[^\n]*', '', body, flags=re.S)
    nums = [int(v.strip(), 0) for v in body.split(',') if v.strip()]
    parts.append('static const unsigned char ' + new + '[] = {\n')
    for n in range(0, len(nums), 12):
        parts.append('    ' + ', '.join(f'0x{x:02x}' for x in nums[n:n+12]) + ',\n')
    parts.append('};\n')
(root / 'src/descriptors.h').write_text(''.join(parts))
