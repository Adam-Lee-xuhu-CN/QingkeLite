# -*- coding: utf-8 -*-
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    with open('data/logs/python_output_20260702_163913.md', 'rb') as f:
        raw = f.read()
    content = raw.decode('utf-8', errors='ignore')
    print(content)
except Exception as e:
    print(f'Error: {e}')
