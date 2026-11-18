# Skydroid S1 bench wiring

[Open the wiring diagram](WIRING.png)

The MCU assignments combine the factory firmware analysis and a disarmed bench run. Verify housing labels, harness pin order, ground, polarity and supply voltage on your exact revision. Connector continuity and output pulse rates have not been measured. Disconnect actuators for the USB firmware workflow.

| Interface | Board configuration | Bench evidence |
| --- | --- | --- |
| USB | PA11/PA12, USB OTG FS, MAVLink2 SERIAL0 | Application CDC and loader tested |
| Internal receiver | PA15/TIM2_CH1 | DataFlash reported SBUS, MAVLink reported 16 RC channels |
| H12 telemetry | USART2 PD5 TX/PD6 RX, MAVLink2 at 921600 | Heartbeat and parameter response through H12 |
| CAN | FDCAN1 PB8 RX/PB9 TX, classic CAN and DroneCAN at 1 Mbit/s | Driver running and TX attempts observed. A second node is pending |
| GPS | USART1 PB14 TX/PB15 RX, SERIAL2 | UART probe traffic observed. GPS fix pending |
| External compass | I2C4 PD12 SCL/PD13 SDA | Verify harness continuity. Factory serial compass uses a vendor protocol |
| Power monitor | ADC1 PC0 voltage/PC3 current, analog monitor 4 | Monitor detected on USB. Loaded calibration pending |
| SD | SDMMC1, FatFS | DataFlash read via MAVFTP |
| Other | SBUS OUT, camera, video, laser, LED and buzzer | Mapping or electrical behavior needs confirmation |

Use the factory CAN harness and appropriate bus termination. For GPS, cross TX/RX and verify the supply and common ground before connecting a module. Confirm battery voltage and current with instruments before trusting the monitor. The SBUS input pin describes the integrated receiver and is separate from the connector marked SBUS OUT.

## Optional tilt-tri profile

| PWM | MCU | Output |
| --- | --- | --- |
| 1 | PE9/TIM1 | Motor1, front right |
| 2 | PE11/TIM1 | Motor2, front left |
| 3 | PE13/TIM1 | Motor4, rear |
| 4 | PE14/TIM1 | Spare |
| 5 | PB6/TIM4 | Front right tilt |
| 6 | PB7/TIM4 | Front left tilt |
| 7 | PD14/TIM4 | Left aileron |
| 8 | PD15/TIM4 | Right aileron |
| 9 | PC6/TIM3 | Left V-tail |
| 10 | PC7/TIM3 | Right V-tail |
| 11 | PB0/TIM3 | Leave physically unused |
| 12 | PB1/TIM3 | Spare |

The example uses normal motor PWM at a configured 400 Hz and 50 Hz for servos. Measure with actuators disconnected and verify electrical ratings, directions, throws and deflection on the actual hardware. Bench telemetry showed 1500 µs on PWM 11 even after its function was set to zero. On restart Plane can assign it function 39, the TRI yaw servo. The example uses the front tilt servos for yaw.
