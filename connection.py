import requests
import time

print("Проверка интернета...\n")

try:
    start = time.time()
    r = requests.get('https://google.com', timeout=10)
    print(f"✅ Google доступен: {r.status_code} ({time.time() - start:.1f} сек)")
except Exception as e:
    print(f"❌ Google недоступен: {e}")

try:
    start = time.time()
    r = requests.get('https://api.telegram.org', timeout=10)
    print(f"✅ Telegram API доступен: {r.status_code} ({time.time() - start:.1f} сек)")
except Exception as e:
    print(f"❌ Telegram API недоступен: {e}")

try:
    import socket

    start = time.time()
    socket.create_connection(('api.telegram.org', 443), timeout=10)
    print(f"✅ Соединение установлено за {time.time() - start:.1f} сек")
except Exception as e:
    print(f"❌ Не могу подключиться: {e}")
