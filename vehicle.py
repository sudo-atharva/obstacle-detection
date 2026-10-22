"""Vehicle interface. Replace SimulatedVehicle with code that drives your real motors."""


class Vehicle:
    def set_speed(self, speed: float):
        """speed is a fraction of full speed, 0..1."""
        raise NotImplementedError

    def stop(self):
        self.set_speed(0.0)


class SimulatedVehicle(Vehicle):
    def __init__(self):
        self.speed = 0.0

    def set_speed(self, speed):
        self.speed = speed


class SerialVehicle(Vehicle):
    """Example for an Arduino or other motor controller on a serial port.

    Sends lines like "S0.30\\n"; write the matching parser in your firmware.
    Requires: pip install pyserial
    """

    def __init__(self, port="/dev/ttyUSB0", baud=115200):
        import serial
        self.conn = serial.Serial(port, baud, timeout=0.1)
        self._last = None

    def set_speed(self, speed):
        speed = round(speed, 2)
        if speed != self._last:  # only send when the speed changes
            self.conn.write(f"S{speed:.2f}\n".encode())
            self._last = speed
