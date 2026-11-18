#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$PWD
commit=fb773567896014da007bca58358704d4a0d8516f
chibios=d103983f4362e5506bcf7062ccacf345fc0c0859
if [[ "$(arm-none-eabi-gcc -dumpfullversion -dumpversion)" != 10.2.1 ]]; then
    echo 'GNU Arm Embedded GCC 10.2.1 is required on PATH.' >&2
    exit 1
fi
if [[ ! -d ardupilot/.git ]]; then
    git clone --depth 1 --branch Plane-4.7.2 https://github.com/ArduPilot/ardupilot.git ardupilot
fi
cd ardupilot
test "$(git rev-parse HEAD)" = "$commit"
git submodule update --init --recursive
test "$(git -C modules/ChibiOS rev-parse HEAD)" = "$chibios"
board=libraries/AP_HAL_ChibiOS/hwdef/skydroid-s1
mkdir -p "$board"
cp "$root/hwdef.dat" "$board/"
patch --batch --output="$board/hal_usb_lld.c" \
    modules/ChibiOS/os/hal/ports/STM32/LLD/OTGv1/hal_usb_lld.c < "$root/patches/otg-driver.patch"
if git apply --reverse --check "$root/patches/ardupilot.patch" 2>/dev/null; then
    echo 'ArduPilot patch already applied.'
else
    git apply "$root/patches/ardupilot.patch"
fi
./waf configure --board skydroid-s1 --no-submodule-update
./waf plane "$@"
mkdir -p "$root/artifacts"
cp build/skydroid-s1/bin/arduplane.apj "$root/artifacts/"
cd "$root/artifacts"
sha256sum arduplane.apj > SHA256SUMS
