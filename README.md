# ArduPlane on the Skydroid S1

A bench-tested ArduPlane 4.7.2 port for the Skydroid S1 with STM32H743 rev V, an ArduPilot-compatible rev 5 bootloader reporting board ID 2003, and a paired Skydroid H12. It preserves the integrated RC and MAVLink paths and adds standard Plane and QuadPlane support.

**Bench only. This build has never flown. Use at your own risk.** Disconnect ESCs and servos before any update, save the factory image and parameters first, and keep the existing bootloader. Compatibility with the S1 Pro, S1-4G and other revisions has yet to be tested. Board ID 2003 alone cannot establish hardware compatibility.

## 1. Prepare a Linux laptop and save the factory setup

Install Git, Python 3 with venv support, Android Platform Tools, patch, a host compiler and GNU Arm Embedded GCC **10.2.1**. Install the build prerequisites documented by [ArduPilot](https://ardupilot.org/dev/docs/building-setup-linux.html). Use an account with access to the serial device, typically via the distribution's `dialout` group.

Enable USB debugging on the H12, connect it by USB and approve its prompt. If Linux denies access, install the following rules in `/etc/udev/rules.d/51-skydroid.rules` as administrator. Use only the line for the vendor ID reported for your H12 by `lsusb`. Add your account to `plugdev` as needed, reload with `sudo udevadm control --reload-rules`, and reconnect the H12.

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="05c6", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0660", GROUP="plugdev"
```

```sh
adb devices
adb shell ls /sdcard/Download
adb pull '/sdcard/Download/SkydroidCopter V2.0.4.apj' factory.apj
sha256sum factory.apj > factory.sha256
```

Substitute the filename on **your own** H12. If the matching factory image is unavailable, stop before attempting a write. Factory APJs are distributed by Skydroid. Verify that your stock application and RC work over S1 USB, then save the parameter set using the parameter export in your GCS. Keep that file private. Save critical SD logs separately. Close other ground stations and serial tools before running the scripts.

## 2. Build or verify the release

From the cloned repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock.txt
./build.sh
```

The script checks the exact [ArduPlane base commit](https://github.com/ArduPilot/ardupilot/tree/fb773567896014da007bca58358704d4a0d8516f), its submodules including pinned ChibiOS, and the compiler version. It applies the board description and patches in this repository before building the application. The ArduPilot waf configure step reports any missing build dependencies. If using the release APJ, place it and its checksum file under `artifacts/` and verify with `(cd artifacts && sha256sum -c SHA256SUMS)`. The flash script also requires the pinned ArduPilot checkout for its uploader.

## 3. Identify, write and verify

Use the actual S1 USB CDC port. The paired H12 should be powered and its sticks centered. Make sure all actuators are disconnected. The identify step restarts the controller into its loader and back into the factory application.

```sh
./flash_s1.sh /dev/ttyACM0 --check-factory
./flash_s1.sh /dev/ttyACM0 --identify-only
./flash_s1.sh /dev/ttyACM0 --write \
  --factory factory.apj --factory-sha256 "$(cut -d ' ' -f1 < factory.sha256)" \
  --image-sha256 "$(awk '$2 == "arduplane.apj" {print $1}' artifacts/SHA256SUMS)"
```

The script checks the disarmed factory application and RC, validates both images, confirms loader board ID 2003 and application capacity, then uploads the application at `0x08020000`. It uses the existing loader's CRC verification. After the write it checks the Plane version, USB MAVLink, IMU, barometer and RC. Use `--apply-bench-profile` only for the optional [tilt-tricopter example](quadplane-bench.param). Keep output 11 physically unused. Plane can reinstate the TRI yaw-servo assignment at startup.

Check radio calibration, failsafe configuration and output assignments on the bench. Retain `SERIAL0_PROTOCOL=2`, `SERIAL1_PROTOCOL=2`, `SERIAL1_BAUD=921` and `SERIAL1_OPTIONS=0` for the tested USB and radio link. Verify battery calibration, GPS/compass, CAN and output behavior before integrating any airframe. See [WIRING](WIRING.md).

## 4. Restore the factory application

Keep the laptop on S1 USB. From a functioning MAVLink link to the disarmed S1, use the GCS bootloader reboot or send `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` with `param1=3` to the S1. If application USB is unavailable, the paired H12 can carry this command over the existing MAVLink radio link. Close clients before using the loader CDC port, which can get a new device name.

```sh
./flash_s1.sh /dev/ttyACM0 --restore-from-loader \
  --factory factory.apj --factory-sha256 "$(cut -d ' ' -f1 < factory.sha256)"
```

The restore checks board ID and verifies the write, factory heartbeat and RC. Restore the saved factory parameters through the GCS and read them back. The first S1 was previously restored using its factory APJ through this bootloader.

## H12 ground stations and performance

The S1 sends MAVLink2 at 921600 baud on USART2. On H12 Android this stream arrives on `/dev/ttyHS1`. A local bridge can open that serial port exclusively and forward MAVLink bidirectionally to a UDP GCS. Close the vendor ground station before opening the serial device. Use a single UDP consumer per port and test one GCS at a time. The test used QGroundControl's UDP receiver. Use the matching configured endpoint for MicoPilot. GCS clients may apply their own firmware-compatibility checks. The bridge and custom Android app will be published later. This repository contains the firmware port.

[Optional packages](h12-optional-packages.txt) lists packages disabled on a bench H12. Review the list for your controller and skip anything you need, especially the store, browser and speech services. For one chosen package:

```sh
adb shell pm disable-user --user 0 PACKAGE
adb shell pm enable --user 0 PACKAGE
```

Use Android Developer Options to set animation scales to 0.5× if desired. Record the original values so they can be restored. Keep Google Play services, system web components, an active keyboard, and your ground stations enabled.

## License

Source changes are available under GPLv3. The ArduPilot and ChibiOS components retain their respective license notices. The corresponding source for the firmware is the pinned upstream source and submodule revisions fetched by the build script together with the board definition and patches here. See [COPYING](COPYING).

## По-русски

Порт ArduPlane 4.7.2 для проверенного S1 с H12 и board ID 2003. В полёте не проверялся. Сохраните заводской APJ со своего H12 и экспортируйте параметры перед записью. Прошивается только приложение, откат выполняется через штатный загрузчик. Схема выходов в [WIRING](WIRING.md).

## En español

Adaptación de ArduPlane 4.7.2 para el S1 probado con H12 e identificador 2003. Solo se ha probado en banco. Antes de escribir, guarde el APJ original de su propio H12 y exporte los parámetros. Se escribe únicamente la aplicación. El cargador original permite restaurarla. Consulte el [esquema](WIRING.md).
