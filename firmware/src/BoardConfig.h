/*
 * SPDX-License-Identifier: MIT
 *
 * XBridge: Adafruit Feather RP2040 with USB Type A Host as an Xbox One /
 * Series X|S controller, driven by a Raspberry Pi over I2C (STEMMA QT port).
 *
 *   USB-C          -> Xbox (the board appears as a wired Xbox controller)
 *   USB-A          -> licensed auth adapter (e.g. Mayflash Magic-X in Xbox mode)
 *   STEMMA QT      -> Raspberry Pi (GND, SDA, SCL only - red wire unused)
 *   Red LED (D13)  -> solid = Pi data arriving, slow blink = waiting for Pi
 */

#ifndef PICO_BOARD_CONFIG_H_
#define PICO_BOARD_CONFIG_H_

#include "enums.pb.h"
#include "class/hid/hid.h"

#define BOARD_CONFIG_LABEL "XBridge (Feather RP2040 USB Host)"

// No physical buttons on this build: every input comes from the Pi.
// Pins used by the Pi link and status LED are reserved for the add-on.
#define GPIO_PIN_02 GpioAction::ASSIGNED_TO_ADDON   // STEMMA QT SDA (I2C1)
#define GPIO_PIN_03 GpioAction::ASSIGNED_TO_ADDON   // STEMMA QT SCL (I2C1)
#define GPIO_PIN_13 GpioAction::ASSIGNED_TO_ADDON   // red LED

// Keyboard-mode mapping (GP2040-CE requires these; unused in Xbox mode)
#define KEY_DPAD_UP     HID_KEY_ARROW_UP
#define KEY_DPAD_DOWN   HID_KEY_ARROW_DOWN
#define KEY_DPAD_RIGHT  HID_KEY_ARROW_RIGHT
#define KEY_DPAD_LEFT   HID_KEY_ARROW_LEFT
#define KEY_BUTTON_B1   HID_KEY_SHIFT_LEFT
#define KEY_BUTTON_B2   HID_KEY_Z
#define KEY_BUTTON_R2   HID_KEY_X
#define KEY_BUTTON_L2   HID_KEY_V
#define KEY_BUTTON_B3   HID_KEY_CONTROL_LEFT
#define KEY_BUTTON_B4   HID_KEY_ALT_LEFT
#define KEY_BUTTON_R1   HID_KEY_SPACE
#define KEY_BUTTON_L1   HID_KEY_C
#define KEY_BUTTON_S1   HID_KEY_5
#define KEY_BUTTON_S2   HID_KEY_1
#define KEY_BUTTON_L3   HID_KEY_EQUAL
#define KEY_BUTTON_R3   HID_KEY_MINUS
#define KEY_BUTTON_A1   HID_KEY_9
#define KEY_BUTTON_A2   HID_KEY_F2
#define KEY_BUTTON_FN   -1

// USB-A host port for the authentication adapter
#define USB_PERIPHERAL_ENABLED 1
#define USB_PERIPHERAL_PIN_DPLUS 16                 // D- is GPIO17
#define USB_PERIPHERAL_PIN_ORDER 0
#define USB_PERIPHERAL_PIN_5V 18                    // 5V boost enable

// Always start as an Xbox One / Series controller
#define DEFAULT_INPUT_MODE INPUT_MODE_XBONE
#define DEFAULT_FORCED_SETUP_MODE FORCED_SETUP_MODE_LOCK_MODE_SWITCH

// Button-combo shortcuts (turbo, D-pad mode changes etc.) are off so no
// keyboard combination can change how the pad behaves mid-game.
#define DEFAULT_LOCK_HOTKEYS true

// Nothing else plugged into the host port, and no display or LEDs
#define KEYBOARD_HOST_ENABLED 0
#define GAMEPAD_USB_HOST_ENABLED 0
#define HAS_I2C_DISPLAY 0
#define I2C0_ENABLED 0
#define I2C1_ENABLED 0
#define BOARD_LEDS_PIN -1

// The Pi link itself
#define PI_LINK_ENABLED 1
#define PI_LINK_PIN_SDA 2
#define PI_LINK_PIN_SCL 3
#define PI_LINK_I2C_BLOCK i2c1
#define PI_LINK_ADDRESS 0x42
#define PI_LINK_PIN_LED 13
#define PI_LINK_TIMEOUT_MS 100
#define PI_LINK_PIN_USB_5V_EN 18

#endif
