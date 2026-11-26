/*
 * SPDX-License-Identifier: MIT
 *
 * XBridge Pi link add-on.
 *
 * Takes a finished controller state (buttons, sticks, triggers) from a
 * Raspberry Pi over I2C and feeds it into the gamepad, so the Xbox driver
 * sends it to the console as if a real pad was plugged in.
 *
 * The board is an I2C *target* (the Pi drives the bus). Packet format
 * (14 bytes, written by the Pi):
 *
 *   [0]      sequence number (wraps)
 *   [1..2]   buttons, little-endian, bits as PILINK_BTN_* below
 *   [3..4]   left stick X   (0 = full left,  65535 = full right)
 *   [5..6]   left stick Y   (0 = full up,    65535 = full down)
 *   [7..8]   right stick X
 *   [9..10]  right stick Y
 *   [11]     left trigger   (0..255)
 *   [12]     right trigger  (0..255)
 *   [13]     CRC-8, poly 0x07, init PILINK_CRC_INIT, over bytes 0..12
 *
 * Reading from the board returns 4 status bytes:
 *   [0] PILINK_STATUS_MAGIC
 *   [1] firmware link version
 *   [2] flags: bit0 USB host (Xbox) has configured us,
 *              bit1 Xbox authentication finished,
 *              bit2 running in Xbox One mode,
 *              bit3 a fresh packet from the Pi is being used
 *   [3] count of bad packets seen (wraps)
 */

#ifndef _PiLink_H
#define _PiLink_H

#include "gpaddon.h"
#include "gamepad.h"

#ifndef PI_LINK_ENABLED
#define PI_LINK_ENABLED 0
#endif

#ifndef PI_LINK_PIN_SDA
#define PI_LINK_PIN_SDA 2
#endif

#ifndef PI_LINK_PIN_SCL
#define PI_LINK_PIN_SCL 3
#endif

// i2c0 or i2c1 - must match the pins above (GPIO2/3 are I2C1 on RP2040)
#ifndef PI_LINK_I2C_BLOCK
#define PI_LINK_I2C_BLOCK i2c1
#endif

#ifndef PI_LINK_ADDRESS
#define PI_LINK_ADDRESS 0x42
#endif

// Status LED (-1 for none). Solid = Pi data arriving, slow blink = waiting.
#ifndef PI_LINK_PIN_LED
#define PI_LINK_PIN_LED -1
#endif

// Pin that switches on 5V for the USB-A host port (-1 for none). Driven
// high firmly at start-up rather than relying on a weak pull-up.
#ifndef PI_LINK_PIN_USB_5V_EN
#define PI_LINK_PIN_USB_5V_EN -1
#endif

// If no good packet arrives for this long, everything is released.
#ifndef PI_LINK_TIMEOUT_MS
#define PI_LINK_TIMEOUT_MS 100
#endif

// Ignore Pi input for this long after power-up so held keys can't trigger
// boot-time mode switches.
#ifndef PI_LINK_BOOT_GUARD_MS
#define PI_LINK_BOOT_GUARD_MS 500
#endif

#define PI_LINK_PACKET_SIZE   14
#define PI_LINK_STATUS_SIZE   4
#define PI_LINK_CRC_INIT      0x5A
#define PILINK_STATUS_MAGIC   0xB7
#define PILINK_VERSION        1

#define PILINK_BTN_A      (1u << 0)
#define PILINK_BTN_B      (1u << 1)
#define PILINK_BTN_X      (1u << 2)
#define PILINK_BTN_Y      (1u << 3)
#define PILINK_BTN_LB     (1u << 4)
#define PILINK_BTN_RB     (1u << 5)
#define PILINK_BTN_VIEW   (1u << 6)
#define PILINK_BTN_MENU   (1u << 7)
#define PILINK_BTN_LS     (1u << 8)
#define PILINK_BTN_RS     (1u << 9)
#define PILINK_BTN_GUIDE  (1u << 10)
#define PILINK_BTN_SHARE  (1u << 11)
#define PILINK_BTN_DUP    (1u << 12)
#define PILINK_BTN_DDOWN  (1u << 13)
#define PILINK_BTN_DLEFT  (1u << 14)
#define PILINK_BTN_DRIGHT (1u << 15)

#define PiLinkName "PiLink"

class PiLinkInput : public GPAddon {
public:
    virtual bool available();
    virtual void setup();
    virtual void process() {}
    virtual void preprocess();
    virtual void postprocess(bool sent) {}
    virtual void reinit() {}
    virtual std::string name() { return PiLinkName; }

    // True once this add-on is running. Used to switch off the button-combo
    // reboot shortcuts, which a keyboard could otherwise hit by accident.
    static bool isActive() { return active; }

    // Exposed for the host-side unit test of the packet format.
    static uint8_t crc8(const uint8_t* data, uint32_t len);

private:
    void updateStatusFlags(bool fresh);
    void updateLed(bool fresh, uint32_t nowMs);

    static bool active;
    uint32_t setupMs = 0;
};

#endif // _PiLink_H
