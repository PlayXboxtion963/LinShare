#!/usr/bin/env python3

# ===== 标准库 =====
import sys
import os
import time
import json
import zipfile
import subprocess
import ssl
import asyncio

# ===== 第三方库 =====
import websockets

# ===== DBus & GLib =====
import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib


SUPPORT_5GHZWIFI = 1

# -----------------------------
# BLE P2P callback with WiFi/WebSocket
# -----------------------------

BLUEZ_SERVICE_NAME = 'org.bluez'
ADAPTER_IFACE = 'org.bluez.Adapter1'
LE_ADV_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'

# -----------------------------
# File Download
# -----------------------------
class P2PCharacteristicHandler:
    def __init__(self):
        self.p2p_ready = False
        self.p2p_info = {}
        self.task_id = None
        self.ws_handshake_done = False

    def handle_write(self, value_bytes):
        try:
            data_str = bytes(value_bytes).decode(errors='ignore')
            print(f"[BLE] Write request received: {data_str}")
            doc = json.loads(data_str)
            self.p2p_info['ssid'] = doc.get('ssid')
            self.p2p_info['psk'] = doc.get('psk')
            self.p2p_info['port'] = doc.get('port')
            self.p2p_info['bssid'] = doc.get('mac')  # 使用 BLE 发送的 MAC 作为 BSSID
            self.p2p_ready = True
            print(f"[P2P] SSID={self.p2p_info['ssid']}, Port={self.p2p_info['port']}, BSSID={self.p2p_info['bssid']}")
            time.sleep(1)
            # 自动触发 WiFi 连接
            gateway_ip = self.connect_wifi(self.p2p_info['ssid'], self.p2p_info['psk'])
            if gateway_ip:
                # WebSocket 和下载处理
                asyncio.run(self.ws_task_loop(gateway_ip, self.p2p_info['port']))
            else:
                print("[P2P] WiFi connection failed, skipping WebSocket")

        except Exception as e:
            print(f"[BLE] Failed to handle write: {e}")


    def connect_wifi(self, ssid, psk):
        subprocess.run(["nmcli", "connection", "delete", ssid],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = subprocess.run([
            "nmcli", "connection", "add",
            "type", "wifi",
            "con-name", ssid,
            "ifname", "*",
            "ssid", ssid,
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", psk
        ], capture_output=True, text=True)
        if result.returncode != 0:
            return None

        result = subprocess.run(["nmcli", "connection", "up", ssid],
                                capture_output=True, text=True)
        if result.returncode != 0:
            return None

        # 获取网关 IP
        try:
            route_result = subprocess.run(
                ["nmcli", "-t", "-f", "IP4.GATEWAY", "device", "show"],
                capture_output=True, text=True, check=True
            )
            for line in route_result.stdout.splitlines():
                line = line.strip()
                # 去掉可能的 "IP4.GATEWAY:" 前缀
                if ":" in line:
                    line = line.split(":", 1)[-1]
                return line.strip()
        except subprocess.CalledProcessError as e:
            print(f"[WiFi] Failed to get gateway: {e}")
            return None
        return None


    async def send_ws_frame(self, ws, payload):
        await ws.send(payload)
        print(f"[WS] Sent: {payload}")

    def download_and_save(self, host, port, task_id, size, file_name):
        # 创建 download 文件夹
        os.makedirs("download", exist_ok=True)
        safe_name = "".join(c if c not in r'<>:"/\|?*' else "_" for c in file_name)
        local_path = os.path.join("download", safe_name)

        url = f"https://{host}:{port}/download?taskId={task_id}"
        print(f"[DL] Downloading {url} -> {local_path}")

        try:
            # 下载文件
            subprocess.run(
                ["wget", "--no-check-certificate", "-O", local_path, url],
                check=True
            )
            print(f"[DL] Download done -> {local_path}")
            os.rename(local_path, local_path+"zip")
            with zipfile.ZipFile(local_path+"zip") as z: z.extractall("download")
            os.remove(local_path+"zip")
                                    

        except subprocess.CalledProcessError as e:
            print(f"[DL] Download failed: {e}")
            local_path = None
        except Exception as e:
            print(f"[DL] Extraction failed: {e}")
            local_path = None

        return local_path

    async def ws_task_loop(self, host, port):
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        uri = f"wss://{host}:{port}/websocket"  
        print(f"[WS] Tring connect: {uri}")
        try:
            async with websockets.connect(uri,ssl=ssl_context) as ws:
                self.ws_handshake_done = True
                print("[WS] Handshake done")
                # 主循环接收消息
                async for message in ws:
                    print(f"[WS] Recv: {message}")
                    if "?" in message:
                        prefix, msg_str = message.split("?", 1)
                        # 修正 JSON 中非法转义，不改变逻辑
                        msg_str = msg_str.replace(r"\*", "*")
                        msg_json = json.loads(msg_str)

                        if "sendRequest" in message:
                            self.task_id = msg_json.get("id")
                            total_size = msg_json.get("totalSize", 0)
                            sender_name = msg_json.get("senderName", "unknown")
                            file_name = msg_json.get("fileName", "file")
                            print(f"[Task] {self.task_id}, from {sender_name}, file {file_name}, {total_size} bytes")
                            local_file = self.download_and_save(host, port, self.task_id, total_size, file_name)

                            status_payload = json.dumps({"taskId": self.task_id, "type":1, "reason":"ok", "id":self.task_id})
                            await self.send_ws_frame(ws, f"action:99:status?{status_payload}")
                            return
                        elif "versionNegotiation" in message:
                            await self.send_ws_frame(ws, 'ack:0:versionNegotiation?{"version":1,"threadLimit":5}')

        except Exception as e:
            print(f"[WS] Connection failed: {e}")

# -----------------------------
# BLE Advertisement
# -----------------------------
class Advertisement(dbus.service.Object):
    PATH_BASE = '/org/bluez/example/advertisement'

    def __init__(self, bus, index, adv_type='peripheral'):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.ad_type = adv_type
        self.service_uuids = ['00003331-0000-1000-8000-008123456789']
        self.manufacturer_data = {}
        self.solicit_uuids = None
        self.service_data = {
            '000001FF-0000-1000-8000-00805f9b34fb': dbus.Array([0xAA,0xAA,SUPPORT_5GHZWIFI,0,0,0], signature='y')
        }
        self.local_name = 'Linux'
        self.include_tx_power = False
        self.scan_response_data = {
            '0000FFFF-0000-1000-8000-00805f9b34fb': dbus.Array(
                [0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xAA,0xAA,0x30,0x31,0x32,0x4C,0x69,0x6E,0x75,0x78,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01],
                signature='y'
            )
        }
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            'Type': self.ad_type,
            'ServiceUUIDs': dbus.Array(self.service_uuids, signature='s'),
            'ServiceData': dbus.Dictionary(self.service_data, signature='sv'),
            'SolicitUUIDs': self.solicit_uuids if self.solicit_uuids else dbus.Array([], signature='s'),
            'ScanResponseServiceData': dbus.Dictionary(self.scan_response_data, signature='sv')
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method('org.freedesktop.DBus.Properties',
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != 'org.bluez.LEAdvertisement1':
            raise dbus.exceptions.DBusException(
                'org.freedesktop.DBus.Error.InvalidArgs: No such interface %s' % interface)
        return self.get_properties()

    @dbus.service.method('org.bluez.LEAdvertisement1', in_signature='', out_signature='')
    def Release(self):
        print('Advertisement released')

# -----------------------------
# GATT Characteristic
# -----------------------------
class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + '/char' + str(index)
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.service = service
        self.value = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            'UUID': self.uuid,
            'Service': dbus.ObjectPath(self.service.get_path()),
            'Flags': dbus.Array(self.flags, signature='s')
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method('org.freedesktop.DBus.Properties',
                         in_signature='s',
                         out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != 'org.bluez.GattCharacteristic1':
            raise dbus.exceptions.DBusException(
                'org.freedesktop.DBus.Error.InvalidArgs: No such interface %s' % interface)
        return self.get_properties()

    @dbus.service.method('org.bluez.GattCharacteristic1',
                         in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        print(f"[BLE] Read request on {self.uuid}")
        return dbus.Array(self.value, signature='y')

    @dbus.service.method('org.bluez.GattCharacteristic1',
                         in_signature='aya{sv}', out_signature='')
    def WriteValue(self, value, options):
        print(f"[BLE] Write request on {self.uuid}: {bytes(value).decode(errors='ignore')}")
        self.value = value
        handler = P2PCharacteristicHandler()
        handler.handle_write(value)

# -----------------------------
# GATT Service
# -----------------------------
class Service(dbus.service.Object):
    PATH_BASE = '/org/bluez/example/service'

    def __init__(self, bus, index, uuid, primary=True):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def add_characteristic(self, char):
        self.characteristics.append(char)

    def get_properties(self):
        return {
            'UUID': self.uuid,
            'Primary': self.primary,
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

# -----------------------------
# GATT Application
# -----------------------------
class Application(dbus.service.Object):
    PATH = '/org/bluez/example/app'

    def __init__(self, bus):
        self.path = self.PATH
        self.bus = bus
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    def add_service(self, service):
        self.services.append(service)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method('org.freedesktop.DBus.ObjectManager',
                         out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        managed_objects = {}
        for service in self.services:
            managed_objects[service.get_path()] = {'org.bluez.GattService1': service.get_properties()}
            for char in service.characteristics:
                managed_objects[char.get_path()] = {'org.bluez.GattCharacteristic1': char.get_properties()}
        return managed_objects

# -----------------------------
# Helper functions
# -----------------------------
def find_adapter(bus):
    obj = bus.get_object(BLUEZ_SERVICE_NAME, '/')
    mgr = dbus.Interface(obj, 'org.freedesktop.DBus.ObjectManager')
    objects = mgr.GetManagedObjects()
    for path, ifaces in objects.items():
        if ADAPTER_IFACE in ifaces:
            return path
    return None

# -----------------------------
# Main
# -----------------------------
def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    adapter_path = find_adapter(bus)
    if not adapter_path:
        print("Bluetooth adapter not found")
        sys.exit(1)

    # --- Advertisement ---
    adapter_obj = bus.get_object(BLUEZ_SERVICE_NAME, adapter_path)
    adv_manager = dbus.Interface(adapter_obj, LE_ADV_MANAGER_IFACE)
    advertisement = Advertisement(bus, 0)
    adv_manager.RegisterAdvertisement(advertisement.get_path(), {},
                                      reply_handler=lambda: print("[+] Advertisement registered"),
                                      error_handler=lambda e: print("Failed to register advertisement:", e))

    # --- GATT Application ---
    gatt_manager = dbus.Interface(adapter_obj, GATT_MANAGER_IFACE)
    app = Application(bus)

    # Example GATT Service
    service = Service(bus, 0, '00009955-0000-1000-8000-00805f9b34fb')
    status_char = Characteristic(bus, 0, '00009954-0000-1000-8000-00805f9b34fb', ['read'], service)
    status_char.value = list(bytearray(json.dumps({"state":0,"key":"","mac":"02:00:00:00:00:00","LinShare":3}), 'utf-8'))
    p2p_char = Characteristic(bus, 1, '00009953-0000-1000-8000-00805f9b34fb', ['write'], service)
    service.add_characteristic(status_char)
    service.add_characteristic(p2p_char)
    app.add_service(service)

    try:
        gatt_manager.RegisterApplication(app.get_path(), {},
                                         reply_handler=lambda: print("[+] GATT service registered"),
                                         error_handler=lambda e: print("Failed to register GATT service:", e))
    except dbus.exceptions.DBusException as e:
        print("GATT registration exception:", e)

    # Main loop
    mainloop = GLib.MainLoop()
    try:
        mainloop.run()
    except KeyboardInterrupt:
        adv_manager.UnregisterAdvertisement(advertisement)
        print("\nAdvertisement unregistered")
        mainloop.quit()

if __name__ == '__main__':
    main()