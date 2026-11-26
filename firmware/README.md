# XBridge firmware

The ready-built file is `XBridge-Feather.uf2`, right here. You only need the rest of this folder to rebuild it.

It's GP2040-CE (open-source controller firmware, MIT licence) with three changes:

- **New add-on** `src/addons/pilink.cpp` + `headers/addons/pilink.h`: listens on I2C
  (address 0x42, the Feather's STEMMA QT socket) for 14-byte controller packets from the Pi
  and feeds them into the gamepad. Releases everything if the Pi goes quiet for 100 ms.
  Also answers status reads (Xbox connected, security check passed) for the settings page.
- **New board** `configs/XBridgeFeather`: Adafruit Feather RP2040 USB Host, Xbox One mode
  by default, USB-A host port on GPIO16/17 with 5V enable on GPIO18 for the security device
  (a Magic-X or a Microsoft/PowerA controller), red LED as the link light, button-combo
  shortcuts locked off.
- **Two small edits**: the add-on is registered in `src/gp2040.cpp` and `CMakeLists.txt`, and the
  hold-buttons reboot shortcut is skipped while the add-on is running (a keyboard could
  otherwise trigger it by accident).

`gp2040-ce-xbridge.patch` holds every change. `src/` has copies of the new files, so you can read them here.

## Rebuild

Base commit: `0ae800d6270c62f5754651074853cc2c648075a9`

    git clone https://github.com/OpenStickCommunity/GP2040-CE.git && cd GP2040-CE
    git checkout 0ae800d6270c62f5754651074853cc2c648075a9
    git submodule update --init lib/tinyusb lib/pico_pio_usb
    git apply /path/to/firmware/gp2040-ce-xbridge.patch

    git clone --branch 2.3.1 https://github.com/raspberrypi/pico-sdk.git ../pico-sdk
    (cd ../pico-sdk && git submodule update --init lib/lwip lib/mbedtls lib/tinyusb)

    # needs: cmake, ninja, arm-none-eabi-gcc (with newlib), python3, node + npm
    export PICO_SDK_PATH=$PWD/../pico-sdk GP2040_BOARDCONFIG=XBridgeFeather SKIP_SUBMODULES=TRUE
    mkdir build && cd build && cmake -G Ninja -DCMAKE_BUILD_TYPE=Release .. && ninja

The output is `build/GP2040-CE_0.0.0_XBridgeFeather.uf2`.

## Test rig

`test-harness.tar.gz` compiles the real add-on on a normal computer against fake hardware.
Unpack it here (`tar xzf test-harness.tar.gz`), run `GP2040=/path/to/patched/GP2040-CE bash test-harness/build.sh`,
then run the Pi tests: they push packets from the Pi code through it and check the exact numbers
the Xbox report gets.
