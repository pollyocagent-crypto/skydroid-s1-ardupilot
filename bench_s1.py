#!/usr/bin/env python3
"""USB-only bench flash and validation for the Skydroid S1."""
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import zlib

os.environ["MAVLINK20"] = "1"
from pymavlink import DFReader, mavutil

PACKAGE = Path(__file__).resolve().parent
UPLOADER = PACKAGE / "ardupilot/Tools/scripts/uploader.py"
sys.path.insert(0, str(UPLOADER.parent))
import uploader

MAV = mavutil.mavlink
BOARD_ID = 2003
APP_BASE = 0x08020000
APP_CAPACITY = (2048 - 128) * 1024
PLANE_CAPACITY = APP_CAPACITY - 256 * 1024


def usb_device(port):
    tty = Path(port).resolve().name
    device = (Path("/sys/class/tty") / tty / "device").resolve(strict=True)
    return next(p for p in (device, *device.parents) if (p / "idVendor").is_file())


def validate_apj(path, digest, capacity):
    contents = path.read_bytes()
    if hashlib.sha256(contents).hexdigest() != digest:
        raise ValueError(f"SHA256 mismatch: {path.name}")
    meta = json.loads(contents)
    image = zlib.decompress(base64.b64decode(meta["image"]))
    external = zlib.decompress(base64.b64decode(meta["extf_image"])) if "extf_image" in meta else b""
    _, reset = struct.unpack_from("<II", image)
    if (meta["magic"] != "APJFWv1" or meta["board_id"] != BOARD_ID
            or len(image) != meta["image_size"] or len(image) % 4
            or not 0 < len(image) <= capacity or not reset & 1
            or not APP_BASE <= reset - 1 < APP_BASE + len(image)
            or meta.get("extf_image_size", 0) or external):
        raise ValueError(f"Invalid S1 application APJ: {path.name}")
    return uploader.firmware(path)


class Application:
    def __init__(self, port):
        self.link = mavutil.mavlink_connection(port, baud=115200, source_system=255, source_component=190)
        self.target = None
        self.ftp_sequence = 0

    def close(self):
        self.link.close()

    def receive(self, kinds, timeout, predicate=lambda _: True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.link.recv_match(type=kinds, blocking=True, timeout=max(0.01, deadline - time.monotonic()))
            if msg is None:
                continue
            if self.target is not None and (msg.get_srcSystem(), msg.get_srcComponent()) != self.target:
                continue
            if predicate(msg):
                return msg
        raise TimeoutError(f"No matching {kinds}")

    def connect(self):
        heartbeat = self.receive("HEARTBEAT", 3, lambda m: m.autopilot == MAV.MAV_AUTOPILOT_ARDUPILOTMEGA
                                 and m.get_srcComponent() == MAV.MAV_COMP_ID_AUTOPILOT1)
        if heartbeat.base_mode & MAV.MAV_MODE_FLAG_SAFETY_ARMED:
            raise ValueError("Controller reports armed")
        self.target = heartbeat.get_srcSystem(), heartbeat.get_srcComponent()
        self.heartbeat()
        return heartbeat

    def heartbeat(self):
        self.link.mav.heartbeat_send(MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, MAV.MAV_STATE_ACTIVE)

    def command(self, command, *args):
        self.link.mav.command_long_send(*self.target, command, 0, *(args + (0,) * (7 - len(args))))

    def request(self, kind, msg_id):
        for _ in range(6):
            self.command(MAV.MAV_CMD_REQUEST_MESSAGE, msg_id)
            try:
                return self.receive(kind, 2)
            except TimeoutError:
                continue
        raise TimeoutError(f"No {kind} response")

    def version(self):
        info = self.request("AUTOPILOT_VERSION", MAV.MAVLINK_MSG_ID_AUTOPILOT_VERSION)
        return bytes(info.flight_custom_version).rstrip(b"\0").decode("ascii"), info.flight_sw_version

    def stream(self, msg_id):
        self.command(MAV.MAV_CMD_SET_MESSAGE_INTERVAL, msg_id, 200000)

    def param(self, name, value=None):
        for _ in range(3):
            if value is None:
                self.link.mav.param_request_read_send(*self.target, name.encode(), -1)
            else:
                self.link.mav.param_set_send(*self.target, name.encode(), value, MAV.MAV_PARAM_TYPE_REAL32)
            try:
                reply = self.receive("PARAM_VALUE", 1, lambda m: m.param_id == name)
            except TimeoutError:
                continue
            if value is not None and not math.isclose(reply.param_value, value, rel_tol=1e-6, abs_tol=1e-6):
                raise RuntimeError(f"{name}: expected {value}, received {reply.param_value}")
            return reply.param_value
        raise TimeoutError(f"No parameter response for {name}")

    def ftp_request(self, opcode, data=b"", session=0, offset=0):
        self.ftp_sequence = (self.ftp_sequence + 1) % 65536
        header = struct.pack(
            "<HBBBBBBI", self.ftp_sequence, session, opcode,
            239 if opcode == 5 else len(data), 0, 0, 0, offset,
        )
        self.link.mav.file_transfer_protocol_send(
            0, *self.target, list((header + data).ljust(251, b"\0"))
        )
        reply = self.receive(
            "FILE_TRANSFER_PROTOCOL", 4,
            lambda msg: bytes(msg.payload)[5] == opcode
            and int.from_bytes(bytes(msg.payload[:2]), "little") == (self.ftp_sequence + 1) % 65536,
        )
        payload = bytes(reply.payload)
        _, session, reply_opcode, size, _, _, _, _ = struct.unpack("<HBBBBBBI", payload[:12])
        data = payload[12:12 + size]
        if opcode == 5 and reply_opcode == 129 and data == b"\x06":
            return session, b""  # End of file
        if reply_opcode != 128:
            raise RuntimeError(f"MAVFTP operation {opcode} failed: {data.hex()}")
        return session, data

    def reboot(self, mode):
        self.command(MAV.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, mode)
        time.sleep(0.3)
        self.close()


def find_port(device):
    for tty in sorted(Path("/sys/class/tty").glob("ttyACM*")):
        port = Path("/dev") / tty.name
        try:
            if port.exists() and usb_device(port) == device:
                return port
        except (FileNotFoundError, StopIteration):
            continue
    raise FileNotFoundError(f"No CDC port at {device.name}")


def connect(device, timeout=22):
    end = time.monotonic() + timeout
    error = None
    while time.monotonic() < end:
        app = None
        try:
            app = Application(str(find_port(device)))
            heartbeat = app.connect()
            print(f"USB heartbeat type={heartbeat.type}, target={app.target}, disarmed")
            return app
        except (OSError, TimeoutError) as exc:
            error = exc
            if app:
                app.close()
            time.sleep(0.4)
    raise TimeoutError(f"No application heartbeat: {error}")


def probe_bootloader(device, timeout=24):
    end = time.monotonic() + timeout
    error = None
    while time.monotonic() < end:
        loader = None
        try:
            loader = uploader.uploader(str(find_port(device)), 115200, [115200], no_extf=True)
            loader.identify()
            print(f"Bootloader board_id={loader.board_type}, board_rev={loader.board_rev}, "
                  f"bl_rev={loader.bl_rev}, flash_size={loader.fw_maxsize}")
            return loader
        except (RuntimeError, OSError) as exc:
            error = exc
            if loader:
                loader.close()
            time.sleep(0.4)
    raise TimeoutError(f"Bootloader identify failed: {error}")


def gate(loader, *images):
    if loader.board_type != BOARD_ID:
        raise ValueError(f"Expected board_id {BOARD_ID}, found {loader.board_type}")
    if loader.fw_maxsize != APP_CAPACITY or any(len(image.image) > loader.fw_maxsize for image in images):
        raise ValueError(f"Unexpected S1 application flash capacity: {loader.fw_maxsize}")


def rc_check(app, factory=False):
    app.stream(MAV.MAVLINK_MSG_ID_RC_CHANNELS)
    samples = []
    for _ in range(5):
        msg = app.receive("RC_CHANNELS", 3, lambda m: m.chancount == 16)
        slots = [getattr(msg, f"chan{i}_raw") for i in range(1, 17)]
        if not all(750 <= value <= 2250 for value in slots):
            raise RuntimeError(f"Invalid RC channel values: {slots}")
        samples.append(slots)
    spread = [max(values) - min(values) for values in zip(*samples)]
    print(f"RC_CHANNELS 16, first={samples[0]}, max variation={max(spread)} us")
    if not factory:
        status = app.request("SYS_STATUS", MAV.MAVLINK_MSG_ID_SYS_STATUS)
        if not status.onboard_control_sensors_health & MAV.MAV_SYS_STATUS_SENSOR_RC_RECEIVER:
            raise RuntimeError("RC receiver health bit is clear")


def check_factory(app):
    version, _ = app.version()
    print(f"Factory application git={version}")
    if version != "64a127d":
        raise ValueError("Unexpected factory firmware identity")
    rc_check(app, factory=True)


def check_plane(app):
    version, sw = app.version()
    print(f"Plane application git={version}, version={(sw >> 24, sw >> 16 & 255, sw >> 8 & 255)}")
    if not version.startswith("fb773567") or (sw >> 8) != 0x040702:
        raise ValueError("Unexpected ArduPlane identity")
    status = app.request("SYS_STATUS", MAV.MAVLINK_MSG_ID_SYS_STATUS)
    required = (MAV.MAV_SYS_STATUS_SENSOR_3D_GYRO | MAV.MAV_SYS_STATUS_SENSOR_3D_ACCEL
                | MAV.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE)
    if status.onboard_control_sensors_health & required != required:
        raise RuntimeError("Gyro, accelerometer or barometer reports unhealthy")
    print("Compass health:", bool(status.onboard_control_sensors_health & MAV.MAV_SYS_STATUS_SENSOR_3D_MAG))
    app.stream(MAV.MAVLINK_MSG_ID_RAW_IMU)
    app.stream(MAV.MAVLINK_MSG_ID_SCALED_PRESSURE)
    imu = [app.receive("RAW_IMU", 2) for _ in range(4)]
    baro = [app.receive("SCALED_PRESSURE", 2) for _ in range(4)]
    g = math.sqrt(imu[0].xacc ** 2 + imu[0].yacc ** 2 + imu[0].zacc ** 2)
    if not 700 <= g <= 1300 or not all(700 <= m.press_abs <= 1150 for m in baro):
        raise RuntimeError("IMU gravity or ambient pressure outside bench range")
    print("IMU samples:", [(m.xacc, m.yacc, m.zacc, m.xgyro, m.ygyro, m.zgyro) for m in imu])
    print("Barometer hPa:", [m.press_abs for m in baro])
    rc_check(app)


def upload(loader, image):
    try:
        gate(loader, image)
        loader.upload(image)
    finally:
        loader.close()


def restore(device, image, check):
    print("Restoring previous application")
    try:
        app = connect(device, timeout=8)
    except TimeoutError:
        pass
    else:
        app.reboot(3)
    upload(probe_bootloader(device), image)
    app = connect(device)
    try:
        check(app)
    finally:
        app.close()
    print("Rollback verified over USB and RC")


def read_profile():
    profile = {}
    for line in (PACKAGE / "quadplane-bench.param").read_text().splitlines():
        if line and not line.startswith("#"):
            name, value = line.split(",")
            profile[name] = float(value)

    return profile


def apply_profile(device, app, profile):
    # Feature enable parameters expose their dependent groups.
    app.param("Q_ENABLE", profile["Q_ENABLE"])
    app.reboot(1)
    app = connect(device)
    try:
        app.param("Q_TILT_ENABLE", profile["Q_TILT_ENABLE"])
        for name, value in profile.items():
            if name not in {"Q_ENABLE", "Q_TILT_ENABLE"}:
                app.param(name, value)
        time.sleep(2)
        app.reboot(1)
    finally:
        app.close()
    app = connect(device)
    try:
        check_plane(app)
        for name, value in profile.items():
            actual = app.param(name)
            if name == "SERVO11_FUNCTION" and actual == 39 and value == 0:
                # TRI startup can replace a disabled yaw servo with its default.
                app.param(name, value)
                actual = app.param(name)
            if not math.isclose(actual, value, rel_tol=1e-6, abs_tol=1e-6):
                raise RuntimeError(f"{name}: expected {value:g} after restart, received {actual:g}")
        app.stream(MAV.MAVLINK_MSG_ID_SERVO_OUTPUT_RAW)
        pwm = app.receive("SERVO_OUTPUT_RAW", 4, lambda m: m.port == 0)
        outputs = {i: getattr(pwm, f"servo{i}_raw") for i in range(1, 13)}
        if not all(800 <= outputs[i] <= 2200 for i in range(5, 11)):
            raise RuntimeError(f"Unexpected disarmed servo outputs: {outputs}")
        print("SERVO_OUTPUT_RAW:", outputs)
        return app
    except Exception:
        app.close()
        raise


def sd_check(app, log_path):
    """Read enough of the new disarmed dataflash log to verify the RC decoder."""
    if not int(app.param("LOG_BACKEND_TYPE")) & 1:
        raise RuntimeError("LOG_BACKEND_TYPE has no file backend")
    original = app.param("LOG_DISARMED")
    app.param("LOG_DISARMED", 1)
    try:
        # The newly enabled logger needs time to flush its startup records.
        for _ in range(6):
            app.heartbeat()
            time.sleep(2)
        app.heartbeat()
        directory = b"APM/LOGS_S1_PLANE"
        session, _ = app.ftp_request(4, directory + b"/LASTLOG.TXT")
        try:
            _, lastlog = app.ftp_request(5, session=session)
            index = lastlog.strip().rstrip(b"D")
            if not index.isdigit():
                raise RuntimeError(f"Invalid SD log index: {lastlog!r}")
            number = int(index)
        finally:
            app.ftp_request(1, session=session)
        path = directory + f"/{number:08d}.BIN".encode()
        session, size = app.ftp_request(4, path)
        data = bytearray()
        try:
            limit = min(int.from_bytes(size, "little"), 220000)
            while len(data) < limit and b"RC Protocol: SBUS" not in data:
                _, chunk = app.ftp_request(5, session=session, offset=len(data))
                if not chunk:
                    break
                data.extend(chunk)
        finally:
            app.ftp_request(1, session=session)
        print(f"SD DataFlash log {number}, read {len(data)} bytes via MAVFTP")
        log_path.write_bytes(data)
        reader = DFReader.DFReader_binary(str(log_path))
        while msg := reader.recv_match(type="MSG"):
            if msg.Message == "RC Protocol: SBUS":
                print(msg.Message)
                return
        raise RuntimeError("SD log did not identify standard SBUS")
    finally:
        app.param("LOG_DISARMED", original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="Current S1 USB application port, e.g. /dev/ttyACM0")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-factory", action="store_true", help="Check factory application without rebooting")
    mode.add_argument("--identify-only", action="store_true", help="Check bootloader and resume factory application")
    mode.add_argument("--write", action="store_true", help="Write Plane with all motors and servos disconnected")
    mode.add_argument("--restore-from-loader", action="store_true", help="Restore factory from an active bootloader")
    parser.add_argument("--factory", type=Path, help="Factory APJ obtained from this controller's H12")
    parser.add_argument("--factory-sha256", help="Recorded SHA256 of the factory APJ")
    parser.add_argument("--image", type=Path, default=PACKAGE / "artifacts/arduplane.apj")
    parser.add_argument("--image-sha256", help="SHA256 from the firmware build or release manifest")
    parser.add_argument("--apply-bench-profile", action="store_true", help="Apply the optional tilt-tri parameters")
    args = parser.parse_args()
    if (args.write or args.restore_from_loader) and not (args.factory and args.factory_sha256):
        parser.error("--factory and --factory-sha256 are required")
    if args.write and not args.image_sha256:
        parser.error("--image-sha256 is required")
    if args.apply_bench_profile and not args.write:
        parser.error("--apply-bench-profile requires --write")
    device = usb_device(args.port)
    factory = validate_apj(args.factory, args.factory_sha256, APP_CAPACITY) if args.factory else None
    if args.restore_from_loader:
        restore(device, factory, check_factory)
        return
    plane = validate_apj(args.image, args.image_sha256, PLANE_CAPACITY) if args.write else None
    app = connect(device)
    try:
        check_factory(app)
        if args.check_factory:
            return
        app.reboot(3)
    finally:
        app.close()

    try:
        loader = probe_bootloader(device)
        try:
            gate(loader, *(image for image in (factory, plane) if image is not None))
            if args.identify_only:
                loader._uploader__reboot()
        except ValueError:
            loader._uploader__reboot()
            raise
        finally:
            loader.close()
    except Exception:
        try:
            current = connect(device, timeout=4)
        except TimeoutError:
            try:
                loader = probe_bootloader(device, timeout=5)
                try:
                    loader._uploader__reboot()
                finally:
                    loader.close()
            except (OSError, TimeoutError, RuntimeError):
                print("Could not resume automatically. Power-cycle only the S1 controller.")
        else:
            current.close()
        raise

    if args.identify_only:
        app = connect(device)
        try:
            check_factory(app)
        finally:
            app.close()
        return

    try:
        upload(probe_bootloader(device), plane)
        app = connect(device)
        try:
            check_plane(app)
            if args.apply_bench_profile:
                app = apply_profile(device, app, read_profile())
        finally:
            app.close()
    except Exception as exc:
        print(f"Plane verification failed: {exc}")
        restore(device, factory, check_factory)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
