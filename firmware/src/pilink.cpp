/*
 * SPDX-License-Identifier: MIT
 *
 * XBridge Pi link add-on - see headers/addons/pilink.h for the wire format.
 */

#include "addons/pilink.h"
#include "storagemanager.h"
#include "drivermanager.h"
#include "drivers/xbone/XBOneDriver.h"

#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "hardware/sync.h"
#include "pico/i2c_slave.h"
#include "pico/time.h"
#include "tusb.h"

#include <string.h>

bool PiLinkInput::active = false;

// ---------------------------------------------------------------------------
// State shared with the I2C interrupt. Everything the interrupt writes is
// only read by the main loop with interrupts briefly off.
// ---------------------------------------------------------------------------
static uint8_t rxBuf[PI_LINK_PACKET_SIZE + 1];
static volatile uint8_t rxLen = 0;
static volatile bool rxOverflow = false;

static uint8_t latestPacket[PI_LINK_PACKET_SIZE];
static volatile uint32_t latestPacketUs = 0;   // time the last good packet landed
static volatile bool havePacket = false;
static volatile uint8_t badPackets = 0;

static uint8_t statusBuf[PI_LINK_STATUS_SIZE] = { PILINK_STATUS_MAGIC, PILINK_VERSION, 0, 0 };
static volatile uint8_t txIdx = 0;

uint8_t PiLinkInput::crc8(const uint8_t* data, uint32_t len) {
    uint8_t crc = PI_LINK_CRC_INIT;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

static inline void __not_in_flash_func(drainRx)(i2c_inst_t* i2c) {
    while (i2c_get_read_available(i2c)) {
        uint8_t b = i2c_read_byte_raw(i2c);
        if (rxLen < sizeof(rxBuf)) {
            rxBuf[rxLen++] = b;
        } else {
            rxOverflow = true;
        }
    }
}

static void __not_in_flash_func(pilinkI2CHandler)(i2c_inst_t* i2c, i2c_slave_event_t event) {
    switch (event) {
        case I2C_SLAVE_RECEIVE:
            drainRx(i2c);
            break;

        case I2C_SLAVE_REQUEST:
            // One byte per request; pad with zeros if the Pi reads too far.
            i2c_write_byte_raw(i2c, txIdx < PI_LINK_STATUS_SIZE ? statusBuf[txIdx] : 0);
            txIdx++;
            break;

        case I2C_SLAVE_FINISH:
            // The SDK can report the stop before the last bytes are read
            // if this interrupt ran late, so collect anything still waiting.
            drainRx(i2c);
            if (rxLen > 0) {
                if (!rxOverflow && rxLen == PI_LINK_PACKET_SIZE &&
                    PiLinkInput::crc8(rxBuf, PI_LINK_PACKET_SIZE - 1) == rxBuf[PI_LINK_PACKET_SIZE - 1]) {
                    memcpy(latestPacket, rxBuf, PI_LINK_PACKET_SIZE);
                    latestPacketUs = time_us_32();
                    havePacket = true;
                } else {
                    badPackets = badPackets + 1;
                }
            }
            rxLen = 0;
            rxOverflow = false;
            txIdx = 0;
            break;
    }
}

bool PiLinkInput::available() {
    return PI_LINK_ENABLED;
}

void PiLinkInput::setup() {
    i2c_inst_t* i2c = PI_LINK_I2C_BLOCK;

    gpio_init(PI_LINK_PIN_SDA);
    gpio_init(PI_LINK_PIN_SCL);
    gpio_set_function(PI_LINK_PIN_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PI_LINK_PIN_SCL, GPIO_FUNC_I2C);
    // The Pi end has only weak pull-ups on its pins, so add ours too.
    gpio_pull_up(PI_LINK_PIN_SDA);
    gpio_pull_up(PI_LINK_PIN_SCL);

    i2c_init(i2c, 400 * 1000); // speed is set by the Pi; this just enables the block
    i2c_slave_init(i2c, PI_LINK_ADDRESS, &pilinkI2CHandler);

#if PI_LINK_PIN_USB_5V_EN >= 0
    // Power the USB-A port for the auth adapter before USB host starts.
    gpio_init(PI_LINK_PIN_USB_5V_EN);
    gpio_set_dir(PI_LINK_PIN_USB_5V_EN, GPIO_OUT);
    gpio_put(PI_LINK_PIN_USB_5V_EN, 1);
#endif

#if PI_LINK_PIN_LED >= 0
    gpio_init(PI_LINK_PIN_LED);
    gpio_set_dir(PI_LINK_PIN_LED, GPIO_OUT);
    gpio_put(PI_LINK_PIN_LED, 0);
#endif

    // Triggers come from the Pi as proper analogue values.
    Storage::getInstance().GetGamepad()->hasAnalogTriggers = true;

    setupMs = to_ms_since_boot(get_absolute_time());
    active = true;
}

void PiLinkInput::updateStatusFlags(bool fresh) {
    uint8_t flags = 0;
    if (tud_mounted()) flags |= 0x01;

    DriverManager& dm = DriverManager::getInstance();
    if (dm.getInputMode() == INPUT_MODE_XBONE) {
        flags |= 0x04;
        GPDriver* driver = dm.getDriver();
        if (driver != nullptr && ((XBOneDriver*)driver)->getAuthSent()) flags |= 0x02;
    }
    if (fresh) flags |= 0x08;

    statusBuf[2] = flags;
    statusBuf[3] = badPackets;
}

void PiLinkInput::updateLed(bool fresh, uint32_t nowMs) {
#if PI_LINK_PIN_LED >= 0
    // Solid when the Pi is talking to us, slow blink when it isn't.
    gpio_put(PI_LINK_PIN_LED, fresh ? 1 : ((nowMs / 500) & 1));
#else
    (void)fresh; (void)nowMs;
#endif
}

static inline uint16_t rd16(const uint8_t* p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

void PiLinkInput::preprocess() {
    Gamepad* gamepad = Storage::getInstance().GetGamepad();
    uint32_t nowMs = to_ms_since_boot(get_absolute_time());

    uint8_t pkt[PI_LINK_PACKET_SIZE];
    bool gotPacket;
    uint32_t pktUs;
    uint32_t irq = save_and_disable_interrupts();
    gotPacket = havePacket;
    pktUs = latestPacketUs;
    memcpy(pkt, latestPacket, sizeof(pkt));
    restore_interrupts(irq);

    bool fresh = gotPacket &&
                 (time_us_32() - pktUs) < (PI_LINK_TIMEOUT_MS * 1000u) &&
                 (nowMs - setupMs) >= PI_LINK_BOOT_GUARD_MS;

    updateStatusFlags(fresh);
    updateLed(fresh, nowMs);

    if (!fresh) {
        // No Pi (or it stopped): leave the pad completely neutral.
        gamepad->state.buttons = 0;
        gamepad->state.dpad = 0;
        gamepad->state.lx = GAMEPAD_JOYSTICK_MID;
        gamepad->state.ly = GAMEPAD_JOYSTICK_MID;
        gamepad->state.rx = GAMEPAD_JOYSTICK_MID;
        gamepad->state.ry = GAMEPAD_JOYSTICK_MID;
        gamepad->state.lt = 0;
        gamepad->state.rt = 0;
        return;
    }

    uint16_t b = rd16(&pkt[1]);

    uint32_t buttons = 0;
    if (b & PILINK_BTN_A)     buttons |= GAMEPAD_MASK_B1;
    if (b & PILINK_BTN_B)     buttons |= GAMEPAD_MASK_B2;
    if (b & PILINK_BTN_X)     buttons |= GAMEPAD_MASK_B3;
    if (b & PILINK_BTN_Y)     buttons |= GAMEPAD_MASK_B4;
    if (b & PILINK_BTN_LB)    buttons |= GAMEPAD_MASK_L1;
    if (b & PILINK_BTN_RB)    buttons |= GAMEPAD_MASK_R1;
    if (b & PILINK_BTN_VIEW)  buttons |= GAMEPAD_MASK_S1;
    if (b & PILINK_BTN_MENU)  buttons |= GAMEPAD_MASK_S2;
    if (b & PILINK_BTN_LS)    buttons |= GAMEPAD_MASK_L3;
    if (b & PILINK_BTN_RS)    buttons |= GAMEPAD_MASK_R3;
    if (b & PILINK_BTN_GUIDE) buttons |= GAMEPAD_MASK_A1;
    if (b & PILINK_BTN_SHARE) buttons |= GAMEPAD_MASK_A2;

    uint8_t dpad = 0;
    if (b & PILINK_BTN_DUP)    dpad |= GAMEPAD_MASK_UP;
    if (b & PILINK_BTN_DDOWN)  dpad |= GAMEPAD_MASK_DOWN;
    if (b & PILINK_BTN_DLEFT)  dpad |= GAMEPAD_MASK_LEFT;
    if (b & PILINK_BTN_DRIGHT) dpad |= GAMEPAD_MASK_RIGHT;

    gamepad->state.buttons = buttons;
    gamepad->state.dpad = dpad;
    gamepad->state.lx = rd16(&pkt[3]);
    gamepad->state.ly = rd16(&pkt[5]);
    gamepad->state.rx = rd16(&pkt[7]);
    gamepad->state.ry = rd16(&pkt[9]);
    gamepad->state.lt = pkt[11];
    gamepad->state.rt = pkt[12];
}
