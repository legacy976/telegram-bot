import requests

ports = [1080, 8080, 3128, 8888, 1081, 1082]

for port in ports:
    try:
        proxies = {
            'http': f'http://127.0.0.1:{port}',
            'https': f'http://127.0.0.1:{port}'
        }
        r = requests.get('https://api.telegram.org', proxies=proxies, timeout=5)
        print(f"✅ Порт {port} работает!")
        print(f"   Используйте в коде: http://127.0.0.1:{port}")
        break
    except:
        print(f"❌ Порт {port} не работает")
