#!/usr/bin/env python3
import asyncio
import base64
import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from uuid import UUID
import zipfile
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.concatkdf import ConcatKDFHash
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import base64
from bleak import BleakScanner, BleakClient
import atexit
import asyncio
import ssl
import websockets
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime
import asyncio
import ssl
import tempfile
import datetime
import aiohttp
from aiohttp import web
from PIL import Image

# -------------------- CONFIG --------------------
SERVICE_UUID = "00003331-0000-1000-8000-008123456789"
CHAR_STATUS_UUID = "00009954-0000-1000-8000-00805f9b34fb"
CHAR_P2P_UUID = "00009953-0000-1000-8000-00805f9b34fb"
SERVER_PORT = 55665
# ------------------------------------------------
if len(sys.argv) < 2:
    print("usage:sudo python sender.py <file>")
    sys.exit(1)

def get_wifi_interface():
    out = subprocess.check_output("iw dev", shell=True, text=True)

    # 找第一个 interface
    m = re.search(r"Interface\s+(\w+)", out)
    if not m:
        raise RuntimeError("No wifi interface found")

    return m.group(1)

INTERFACE = get_wifi_interface()
FILES = sys.argv[1:]

if not FILES:
    print("usage: sender.py <files...>")
    sys.exit(1)

for f in FILES:
    if not os.path.isfile(f):
        print("file not found:", f)
        sys.exit(1)

print(f"[INFO] Use {INTERFACE} to send {FILES}")

FILE_COUNT = len(FILES)
FILE_SIZE_ALL = sum(os.path.getsize(f) for f in FILES)

# 单文件
if FILE_COUNT == 1:
    FILE_PATH = FILES[0]
    FILE_NAME = os.path.basename(FILE_PATH)
    MIME_TYPE = (
        mimetypes.guess_type(FILE_PATH)[0]
        or "application/octet-stream"
    )

# 多文件自动 zip
else:
    FILE_NAME = "files.zip"
    MIME_TYPE = "application/zip"


# THUMBNAIL
def build_thumbnail(file_path):
    try:
        img = Image.open(file_path)

        img.thumbnail((320, 320))

        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        out = io.BytesIO()

        img.save(out, format="PNG")

        return out.getvalue()

    except Exception as e:
        print(e)
        return None


THUMBNAIL_DATA = (
    build_thumbnail(FILES[0])
    if FILE_COUNT == 1
    else None
)


# ZIP
def zip_file(files):
    zip_path = os.path.join(
        tempfile.gettempdir(),
        os.path.basename(files[0]) + ".zip"
    )

    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED
    ) as z:
        for f in files:
            z.write(
                f,
                arcname=os.path.basename(f)
            )

    return zip_path


# DOWNLOAD
async def download_file(request):
    task_id = request.query.get("taskId")
    print("download task:", task_id)
    return web.FileResponse(zip_file(FILES))

async def thumbnail(request):
    task_id = request.query.get("taskId")

    print("thumbnail task:", task_id)
    return web.Response(
        body=THUMBNAIL_DATA,
        content_type="image/png",
        headers={
            "Content-Length": str(len(THUMBNAIL_DATA))
        }
    )

async def run_websocket_server(domains=None, key_password=b"foobar", cert_password="foobar", host="0.0.0.0"):
    if domains is None:
        domains = ["127.0.0.1", "0.0.0.0", "localhost","10.42.0.1"]
    try:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
        cert = (x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(private_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now() - datetime.timedelta(days=1))
                .not_valid_after(datetime.datetime.now() + datetime.timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]), critical=False)
                .sign(private_key, hashes.SHA256()))
        cert_pem = cert.public_bytes(encoding=serialization.Encoding.PEM)
        key_pem = private_key.private_bytes(encoding=serialization.Encoding.PEM,
                                            format=serialization.PrivateFormat.PKCS8,
                                            encryption_algorithm=serialization.BestAvailableEncryption(key_password))

        with tempfile.NamedTemporaryFile(mode='wb', suffix='.crt', delete=False) as cf:
            cf.write(cert_pem)
            cert_path = cf.name
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.key', delete=False) as kf:
            kf.write(key_pem)
            key_path = kf.name

        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(cert_path, key_path, password=cert_password)
        async def websocket_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            print(f"[服务器] 客户端已连接: {request.remote}")
            await ws.send_str( 'action:0:versionNegotiation?{"version":1,"versions":[1]}')
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        print(f"[服务器] 收到消息 (来自 {request.remote}): {msg.data}")
                        if "versionNego" in msg.data:
                            if THUMBNAIL_DATA is None:
                                await ws.send_str(
                                f'action:1:sendRequest?{{'
                                f'"taskId":"123456",'
                                f'"id":"123456",'
                                f'"senderId":"AABB",'
                                f'"senderName":"Linux",'
                                f'"fileName":"{FILE_NAME}",'
                                f'"mimeType":"{MIME_TYPE}",'
                                f'"fileCount":{FILE_COUNT},'
                                f'"totalSize":{FILE_SIZE_ALL}'
                                f'}}'
                                ) 
                            else:
                                await ws.send_str(
                                f'action:1:sendRequest?{{'
                                f'"taskId":"123456",'
                                f'"id":"123456",'
                                f'"senderId":"AABB",'
                                f'"senderName":"Linux",'
                                f'"fileName":"{FILE_NAME}",'
                                f'"mimeType":"{MIME_TYPE}",'
                                f'"fileCount":{FILE_COUNT},'
                                f'"totalSize":{FILE_SIZE_ALL},'
                                f'"thumbnail":"/thumbnail?taskId=123456",'
                                f'"thumbnail_height":320,'
                                f'"thumbnail_width":320'
                                f'}}'
                                )
                        elif "\"type\":1" in msg.data:
                            print("User Accept Task")
                        elif "\"type\":3" in msg.data:
                            print("User Reject")
                        elif "action:99" in msg.data:
                            print("Download Finish ,Exit")    
                        
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        print(f"[服务器] WebSocket错误 (来自 {request.remote}): {ws.exception()}")
            finally:
                print(f"[服务器] 客户端已断开: {request.remote}")
                sys.exit(0)
            return ws

        app = web.Application()
        app.router.add_get("/websocket", websocket_handler)
        app.router.add_get("/download", download_file)
        app.router.add_get("/thumbnail", thumbnail)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=SERVER_PORT, ssl_context=ssl_context)
        await site.start()

        actual_port = site._server.sockets[0].getsockname()[1]
        print(f"[服务器] 启动于 wss://{host}:{actual_port}/ws")
        await asyncio.Future()
    except Exception as e:
        print(f"[服务器] 启动失败: {e}")
# ----------- SERVICE DATA PARSER ---------------
def parse_service_data(record):
    sender_id = None
    device_name = None
    supports5Ghz = False
    brandId = None

    for uuid_str, data in record.items():
        if len(data) == 6:
            arr = bytearray(16)
            u = UUID(uuid_str)
            arr[0:8] = u.int.to_bytes(16, 'big')[0:8]
            arr[8:16] = u.int.to_bytes(16, 'big')[8:16]
            supports5Ghz = arr[2] == 1
            brandId = arr[3]

        elif len(data) == 27:
            name_bytes = bytearray()
            for i in range(10, 26):
                if data[i] != 0:
                    name_bytes.append(data[i])
                else:
                    break
            sender_id_raw = (data[8] << 8) | data[9]
            sender_id = f"{sender_id_raw:04x}"
            name = name_bytes.decode(errors="ignore")
            if name.endswith("\t"):
                name = name[:-1] + "..."
            device_name = name

    return sender_id, device_name, supports5Ghz, brandId

# ----------- BLE SCAN --------------------------
async def scan_devices():
    print("[SCAN] Scanning BLE devices for 10 seconds...")
    devices = await BleakScanner.discover(timeout=10.0)
    matching_devices = []

    for device in devices:
        uuids = getattr(device, "metadata", {}).get("uuids", [])
        if not uuids:
            uuids = getattr(device, "details", {}).get("props", {}).get("UUIDs", [])
        uuids = [u.lower() for u in uuids]

        if SERVICE_UUID.lower() in uuids:
            service_data = getattr(device, "metadata", {}).get("service_data", {})
            if not service_data:
                service_data = getattr(device, "details", {}).get("props", {}).get("ServiceData", {})
            sender_id, device_name, supports5Ghz, brandId = parse_service_data(service_data)
            matching_devices.append((device, sender_id, device_name, supports5Ghz))

    if not matching_devices:
        raise RuntimeError("No matching devices found")

    print("[DEVICES] Found devices:")
    for i, (d, sid, name, _) in enumerate(matching_devices):
        print(f"{i}: {name or 'Unknown'} ({d.address}) senderId={sid}")

    idx = int(input("Select device number to connect: "))
    return matching_devices[idx]

# ----------- BLE SECURITY ----------------------
class BleSecurity:
    # ----------------- 本地 EC 密钥 -----------------
    local_private_key = ec.generate_private_key(ec.SECP256R1())
    local_public_key = local_private_key.public_key()

    @staticmethod
    def get_encoded_public_key() -> str:
        """
        获取本地公钥，DER 编码后 Base64 输出，直接给 Kotlin 使用
        """
        pub_bytes = BleSecurity.local_public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(pub_bytes).decode('ascii')  # ASCII 保证 Kotlin 可解码

    @staticmethod
    def derive_session_key(peer_public_key_b64: str):
        peer_pub_bytes = base64.b64decode(peer_public_key_b64)
        peer_public_key = serialization.load_der_public_key(peer_pub_bytes)
        shared_key = BleSecurity.local_private_key.exchange(ec.ECDH(), peer_public_key)
        # 直接返回共享密钥（32 字节）
        return BleSecurity.SessionCipher(shared_key)

    # ----------------- 会话加密器 -----------------
    class SessionCipher:
        def __init__(self, key_bytes: bytes):
            self.key = key_bytes
            self.iv = b"0102030405060708"  # 固定 IV，与 Kotlin 保持一致

        def encrypt(self, data: str) -> str:
            """
            加密，返回标准 Base64 字符串
            """
            cipher = Cipher(algorithms.AES(self.key), modes.CTR(self.iv))
            encryptor = cipher.encryptor()
            ct = encryptor.update(data.encode('utf-8')) + encryptor.finalize()
            return base64.b64encode(ct).decode('ascii')  # ASCII 保证 Kotlin 可解

        def decrypt(self, encoded_data: str) -> str:
            """
            解密 Base64 数据
            """
            ct = base64.b64decode(encoded_data)
            cipher = Cipher(algorithms.AES(self.key), modes.CTR(self.iv))
            decryptor = cipher.decryptor()
            data = decryptor.update(ct) + decryptor.finalize()
            return data.decode('utf-8')

# ----------- WIFI DIRECT -----------------------
def sh(cmd):
    return subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True
    ).stdout.strip()

def start_dhcp_server(interface):

    def run():

        conf = f"""
start 192.168.49.2
end 192.168.49.2

interface {interface}

opt subnet 255.255.255.0
opt router 192.168.49.1
opt dns 192.168.49.1

option lease 6000

pidfile /tmp/udhcpd.pid
lease_file /tmp/udhcpd.leases
"""

        file = tempfile.NamedTemporaryFile(delete=False)

        file.write(conf.encode())
        file.close()

        subprocess.Popen(
            f"sudo udhcpd -f {file.name}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    threading.Thread(target=run, daemon=True).start()
    
def create_p2p(interface=INTERFACE, conf="p2p.conf"):
    sh("sudo killall wpa_supplicant dnsmasq")
    sh("sudo systemctl stop NetworkManager systemd-resolved")
    sh("sudo rm -rf /var/run/wpa_supplicant/*")

    # 后台启动
    subprocess.Popen(
        f"sudo wpa_supplicant "
        f"-i {interface} "
        f"-c {conf}",
        shell=True
    )

    # 等待接口 ready
    while "PONG" not in sh(f"sudo wpa_cli -i {interface} ping"):
        time.sleep(0.1)

    # 建组
    sh(f"sudo wpa_cli -i {interface} p2p_group_add")

    # 等待 GO 启动完成
    while "freq=" not in sh(f"sudo wpa_cli -i {interface} status"):
        time.sleep(0.1)

    # 配 IP
    sh(f"sudo ip addr flush dev {interface}")
    sh(f"sudo ip addr add 192.168.49.1/24 dev {interface}")
    sh(f"sudo ip link set {interface} up")

    # DHCP
    start_dhcp_server(interface)

    info = sh(f"sudo wpa_cli -i {interface} status")

    return (
        sh(f"echo '{info}' | grep ^ssid= | cut -d= -f2"),
        sh(f"sudo wpa_cli -i {interface} p2p_get_passphrase"),
        sh(f"echo '{info}' | grep ^p2p_device_address= | cut -d= -f2"),
        sh(f"echo '{info}' | grep ^freq= | cut -d= -f2"),
    )


def restore_network(interface=INTERFACE):
    sh("sudo killall wpa_supplicant dnsmasq")
    sh(f"sudo ip addr flush dev {interface}")
    sh("sudo systemctl restart systemd-resolved")
    sh("sudo systemctl restart NetworkManager")


atexit.register(restore_network)
# ----------- BLE READ/WRITE -------------------
async def read_write_ble(device):
    ssid, psk, mac,freq = create_p2p()
    time.sleep(1)
    asyncio.create_task(run_websocket_server(host="0.0.0.0"))
    try:
        async with BleakClient(device.address) as client:
            # await client.connect()
            print(f"[CONNECT] Connected to {device.name} ({device.address})")

            # 读取 CHAR_STATUS_UUID
            try:
                raw = await client.read_gatt_char(CHAR_STATUS_UUID)
                data_str = raw.decode()
                print(f"[CHAR READ] {data_str}")
                info = json.loads(data_str)
                # 提取对方 MAC 地址
                peer_mac = info.get("mac")
                if peer_mac:
                    print(f"[INFO] Peer MAC: {peer_mac}")
                    # 调用 wpa_cli p2p_listen

            except Exception as e:
                print(f"[WARN] Failed to read status char: {e}")
                info = {}

           
            # 派生 session key
            cipher = None
            if "key" in info and info["key"]:
                ble_sec = BleSecurity()
                cipher = ble_sec.derive_session_key(info["key"])
                pub_key = ble_sec.get_encoded_public_key()
            else:
                pub_key = None

            # 构建 P2P info
            print(f"[Local Info Before] {ssid} {psk} {mac}")
            new_p2p_info = {
                "id": "AABB",
                "ssid": cipher.encrypt(ssid) if cipher else ssid,
                "psk":  cipher.encrypt(psk) if cipher else psk,
                "mac":  cipher.encrypt(mac) if cipher else mac,
                "key": pub_key,
                "freq":freq,
                "port": SERVER_PORT,
            }
            print(f"[Local Info ] {new_p2p_info}")
            # 写入 CHAR_P2P_UUID
            try:
                await client.write_gatt_char(CHAR_P2P_UUID, json.dumps(new_p2p_info).encode())
                print("[CHAR WRITE] P2P info written successfully")
                await asyncio.Future()
            except Exception as e:
                print(f"[ERROR] Failed to write P2P info: {e}")

    except Exception as e:
        print(f"[ERROR] BLE connection failed: {e}")

# ----------- MAIN -----------------------------
async def main():

    device, sender_id, device_name, supports5Ghz = await scan_devices()
    print(f"[INFO] Selected device: {device_name}, senderId={sender_id}")

    await read_write_ble(device)



if __name__ == "__main__":
    asyncio.run(main())