import socket

def get_local_ip_addresses():
    """
    Finds the primary local LAN IPv4 address(es) for smartphone access.
    """
    ip_list = []
    # Primary outgoing IP determination
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Doesn't actually send packets over the internet
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and not primary_ip.startswith("127."):
            ip_list.append(primary_ip)
    except Exception:
        pass

    # Secondary lookup via hostname
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ip not in ip_list:
                ip_list.append(ip)
    except Exception:
        pass

    if not ip_list:
        ip_list.append("127.0.0.1")

    return ip_list
