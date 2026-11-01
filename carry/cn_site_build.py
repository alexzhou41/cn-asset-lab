# -*- coding: utf-8 -*-
"""Build a self-contained static site from the Mini App client + computed payload.

The Mini App Node entry inlines data.json at request time; a static deployment
has no server, so the payload is baked into the HTML here.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, '..'))
CLIENT = os.path.join(WS, 'miniapps', 'china-all-weather', 'miniapp', 'client')
SITE = os.path.join(WS, 'site_cn_allweather')

with open(os.path.join(CLIENT, 'index.html'), encoding='utf-8') as f:
    page = f.read()
with open(os.path.join(CLIENT, 'data.json'), encoding='utf-8') as f:
    payload = f.read()

assert '__DATA_JSON__' in page, 'placeholder missing from client page'
safe = payload.replace('<\/', '<\\/').replace('</', '<\\/')
html = page.replace('__DATA_JSON__', safe)

os.makedirs(SITE, exist_ok=True)
out = os.path.join(SITE, 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)

print('wrote', out, f'({os.path.getsize(out)/1024:.0f} KB)')
print('data payload', f'{len(payload)/1024:.0f} KB')
