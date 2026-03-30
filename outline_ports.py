import socket
import socks


def find_socks5_port():
    """Найти порт, на котором работает SOCKS5"""

    for port in range(1080, 1100):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()

            if result == 0:
                print(f"Порт {port} открыт, проверяем SOCKS5...")

                try:
                    socks.set_default_proxy(socks.SOCKS5, "127.0.0.1", port)
                    socket.socket = socks.socksocket

                    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    test_sock.settimeout(2)
                    test_sock.connect(("api.telegram.org", 443))
                    test_sock.close()

                    return port
                except:
                    print(f"  Порт {port} не SOCKS5")
        except:
            continue

    return None


port = find_socks5_port()
if port:
    print(f"\n✅ Найден SOCKS5 порт: {port}")
    print(f"\n📝 Используйте в коде:")
    print(f"""
import telebot.apihelper
telebot.apihelper.proxy = {{
    'http': 'socks5://127.0.0.1:{port}',
    'https': 'socks5://127.0.0.1:{port}'
}}
    """)
else:
    print("\n❌ SOCKS5 прокси не найден")
    print("\n💡 Решение:")
    print("1. Включите локальный прокси в настройках Outline VPN")
    print("2. Или используйте Outline VPN как обычный VPN без прокси")
    print("3. Просто запустите бота без настройки прокси")
