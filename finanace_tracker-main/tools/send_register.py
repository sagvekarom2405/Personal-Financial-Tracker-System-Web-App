import json
import urllib.request

url = 'http://127.0.0.1:5000/register'
payload = {
    'email': 'verifyme+1@example.local',
    'password': 'Pass1234',
    'full_name': 'Verify Me',
    'phone': '9876543210'
}

data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode('utf-8'))
