"""
Push vacancy data from role_vacancy_clean.csv to a remote TNPD server.

Usage:
    python scripts/push_vacancy_to_remote.py [server_url] [username] [password] [csv_path]

Defaults:
    server_url = https://dev-tnprisons.giggso.com
    username   = admin
    password   = Hello@123
    csv_path   = role_vacancy_clean.csv
"""

import sys
import json
import urllib.request
import urllib.parse
import urllib.error

if len(sys.argv) < 4:
    print('Usage: python push_vacancy_to_remote.py <server_url> <username> <password> [csv_path]')
    print('  e.g. python scripts/push_vacancy_to_remote.py https://dev-tnprisons.giggso.com admin <password>')
    sys.exit(1)

SERVER   = sys.argv[1]
USER     = sys.argv[2]
PASSWD   = sys.argv[3]
CSV_PATH = sys.argv[4] if len(sys.argv) > 4 else 'role_vacancy_clean.csv'


def post_json(url, data, cookie=None):
    payload = json.dumps(data).encode()
    headers = {'Content-Type': 'application/json'}
    if cookie:
        headers['Cookie'] = cookie
    req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), {}


# Step 1: login to get session cookie
print(f'Logging in to {SERVER} as {USER}...')
resp, headers = post_json(f'{SERVER}/web/session/authenticate', {
    'jsonrpc': '2.0', 'method': 'call', 'id': 1,
    'params': {'db': 'tnpd-prison-db', 'login': USER, 'password': PASSWD},
})
if resp.get('result', {}).get('uid'):
    uid = resp['result']['uid']
    print(f'  Logged in as uid={uid}')
else:
    print('  Login failed:', resp)
    sys.exit(1)

cookie = headers.get('Set-Cookie', '')
if 'session_id=' in cookie:
    sid = cookie.split('session_id=')[1].split(';')[0]
    cookie = f'session_id={sid}'
else:
    print('  Could not extract session cookie. Headers:', list(headers.items())[:5])
    sys.exit(1)

# Step 2: read CSV
with open(CSV_PATH, 'r', encoding='utf-8') as f:
    csv_text = f.read()
print(f'CSV loaded: {len(csv_text.splitlines())} lines')

# Step 3: POST to import-csv with clear_roles=True
print('Clearing existing canonical roles and importing from CSV...')
resp, _ = post_json(
    f'{SERVER}/api/vacancy/import-csv',
    {'csv': csv_text, 'clear_roles': True},
    cookie=cookie,
)
if resp.get('success'):
    r = resp
    print(f'  total={r.get("total")}  created={r.get("created")}  '
          f'updated={r.get("updated")}  failed={r.get("failed")}')
    if r.get('errors'):
        print('  Errors (first 10):')
        for e in r['errors'][:10]:
            print(f'    {e}')
else:
    print('  Import failed:', resp)
    sys.exit(1)

print('Done.')
