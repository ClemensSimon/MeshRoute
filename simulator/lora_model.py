"""
LoRa radio model for MeshRoute simulator.
Models path loss, RSSI, SNR, packet success rate, time-on-air,
duty cycle enforcement, and channel collision for EU 868MHz LoRa.
"""

import math

# EU 868MHz regulations
DUTY_CYCLE = 0.01  # 1% duty cycle limit
DUTY_CYCLE_WINDOW = 3600.0  # 1 hour window in seconds
MAX_PAYLOAD = 256  # bytes
FREQUENCY = 868e6  # Hz
TX_POWER = 14  # dBm (typical LoRa)
NOISE_FLOOR = -120  # dBm (typical LoRa receiver)

# Log-distance path loss model parameters (urban environment)
PL_D0 = 1.0  # reference distance (m)
PL_N = 2.8  # path loss exponent (2.8 for urban, 2.0 for free space)
PL_SIGMA = 6.0  # shadow fading std dev (dB), used for stochastic model
# Reference path loss at d0=1m for 868MHz (Friis)
PL_REF = 20 * math.log10(4 * math.pi * PL_D0 * FREQUENCY / 3e8)

# Path loss exponents for different terrain types
TERRAIN_PL_EXPONENTS = {
    "free_space": 2.0,
    "rural": 2.4,
    "suburban": 2.8,
    "urban": 3.2,
    "dense_urban": 3.5,
    "indoor": 3.8,
}

# Spreading factor parameters: SF -> (sensitivity_dBm, data_rate_bps at BW125)
SF_PARAMS = {
    7:  (-124.0, 5470),
    8:  (-127.0, 3125),
    9:  (-130.0, 1760),
    10: (-133.0, 980),
    11: (-135.0, 440),
    12: (-137.0, 250),
}


def path_loss(distance, frequency=FREQUENCY, terrain="urban"):
    """Log-distance path loss model with terrain support.

    Args:
        distance: Distance in meters (must be > 0)
        frequency: Carrier frequency in Hz
        terrain: Terrain type (free_space, rural, suburban, urban, dense_urban, indoor)

    Returns:
        Path loss in dB
    """
    if distance <= 0:
        return 0.0
    if distance < PL_D0:
        distance = PL_D0
    pl_n = TERRAIN_PL_EXPONENTS.get(terrain, PL_N)
    pl_ref = 20 * math.log10(4 * math.pi * PL_D0 * frequency / 3e8)
    return pl_ref + 10 * pl_n * math.log10(distance / PL_D0)


def snr_from_rssi(rssi):
    """Estimate SNR from RSSI.

    Args:
        rssi: Received signal strength in dBm

    Returns:
        SNR in dB
    """
    return rssi - NOISE_FLOOR


def packet_success_rate(rssi):
    """Probability of successful packet reception based on RSSI.

    Uses a sigmoid model centered around the sensitivity threshold.
    LoRa SF7 sensitivity is about -124 dBm, but we use a practical
    threshold of -120 dBm for reliable operation.

    Args:
        rssi: RSSI in dBm

    Returns:
        Probability of success (0.0 to 1.0)
    """
    # Sensitivity threshold (dBm) - below this, reception is unreliable
    threshold = -120.0
    # Steepness of the sigmoid transition
    k = 0.5
    # Sigmoid: approaches 1 well above threshold, 0 well below
    x = rssi - threshold
    try:
        return 1.0 / (1.0 + math.exp(-k * x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def time_on_air(payload_bytes, sf=7, bw=125e3, cr=1, preamble=8, explicit_header=True, crc=True):
    """Calculate LoRa time-on-air for a packet.

    Based on Semtech SX1276 datasheet formula.

    Args:
        payload_bytes: Payload size in bytes
        sf: Spreading factor (7-12)
        bw: Bandwidth in Hz (125000, 250000, 500000)
        cr: Coding rate (1-4, where 1 = 4/5, 4 = 4/8)
        preamble: Preamble symbol count
        explicit_header: Whether explicit header is used
        crc: Whether CRC is enabled

    Returns:
        Time on air in seconds
    """
    # Symbol duration
    t_sym = (2 ** sf) / bw

    # Preamble duration
    t_preamble = (preamble + 4.25) * t_sym

    # Payload symbols
    de = 1 if sf >= 11 else 0  # low data rate optimization
    ih = 0 if explicit_header else 1
    crc_bits = 16 if crc else 0

    numerator = 8 * payload_bytes - 4 * sf + 28 + crc_bits - 20 * ih
    denominator = 4 * (sf - 2 * de)

    n_payload = 8 + max(math.ceil(numerator / denominator) * (cr + 4), 0)

    t_payload = n_payload * t_sym

    return t_preamble + t_payload


def max_range_meters(tx_power=TX_POWER, threshold_rssi=-120.0):
    """Calculate maximum theoretical range (legacy, uses urban terrain).

    Args:
        tx_power: Transmit power in dBm
        threshold_rssi: Minimum RSSI for reception in dBm

    Returns:
        Maximum range in meters
    """
    max_pl = tx_power - threshold_rssi
    pl_ref = 20 * math.log10(4 * math.pi * PL_D0 * FREQUENCY / 3e8)
    exponent = (max_pl - pl_ref) / (10 * PL_N)
    return PL_D0 * (10 ** exponent)


def link_quality_from_distance(distance, tx_power=TX_POWER, terrain="urban"):
    """Compute a 0-1 link quality score from distance.

    Args:
        distance: Distance in meters
        tx_power: Transmit power in dBm
        terrain: Terrain type

    Returns:
        Quality score (0.0 to 1.0)
    """
    rssi = rssi_from_distance(distance, tx_power, terrain=terrain)
    return packet_success_rate(rssi)


def rssi_from_distance(distance, tx_power=TX_POWER, terrain="urban"):
    """Calculate received signal strength from distance.

    Args:
        distance: Distance in meters
        tx_power: Transmit power in dBm
        terrain: Terrain type

    Returns:
        RSSI in dBm
    """
    return tx_power - path_loss(distance, terrain=terrain)


def sensitivity_for_sf(sf=7):
    """Return receiver sensitivity in dBm for a given spreading factor."""
    return SF_PARAMS.get(sf, SF_PARAMS[7])[0]


def max_range_for_sf(sf=7, tx_power=TX_POWER, terrain="urban"):
    """Calculate maximum range for a given SF and terrain.

    Args:
        sf: Spreading factor (7-12)
        tx_power: Transmit power in dBm
        terrain: Terrain type

    Returns:
        Maximum range in meters
    """
    threshold = sensitivity_for_sf(sf)
    max_pl = tx_power - threshold
    pl_n = TERRAIN_PL_EXPONENTS.get(terrain, PL_N)
    pl_ref = 20 * math.log10(4 * math.pi * PL_D0 * FREQUENCY / 3e8)
    exponent = (max_pl - pl_ref) / (10 * pl_n)
    return PL_D0 * (10 ** exponent)


class DutyCycleTracker:
    """Tracks per-node airtime usage and enforces EU 868MHz 1% duty cycle.

    Each node has a rolling window of transmissions. A node can only transmit
    if its total airtime in the last DUTY_CYCLE_WINDOW seconds is below
    DUTY_CYCLE * DUTY_CYCLE_WINDOW.
    """

    def __init__(self, max_airtime_per_window=None):
        self.max_airtime = max_airtime_per_window or (DUTY_CYCLE * DUTY_CYCLE_WINDOW)
        # node_id -> list of (timestamp, duration) tuples
        self._tx_log = {}
        self.violations = 0

    def can_transmit(self, node_id, current_time, payload_bytes=50, sf=7):
        """Check if a node can transmit without exceeding duty cycle.

        Args:
            node_id: Node identifier
            current_time: Current simulation time in seconds
            payload_bytes: Packet payload size
            sf: Spreading factor

        Returns:
            True if transmission is allowed
        """
        toa = time_on_air(payload_bytes, sf=sf)
        used = self._get_airtime(node_id, current_time)
        return (used + toa) <= self.max_airtime

    def record_tx(self, node_id, current_time, payload_bytes=50, sf=7):
        """Record a transmission for duty cycle tracking.

        Returns:
            Time-on-air in seconds, or 0 if duty cycle would be exceeded
        """
        toa = time_on_air(payload_bytes, sf=sf)
        used = self._get_airtime(node_id, current_time)

        if (used + toa) > self.max_airtime:
            self.violations += 1
            return 0.0

        if node_id not in self._tx_log:
            self._tx_log[node_id] = []
        self._tx_log[node_id].append((current_time, toa))
        return toa

    def _get_airtime(self, node_id, current_time):
        """Get total airtime used by a node in the current window."""
        if node_id not in self._tx_log:
            return 0.0

        window_start = current_time - DUTY_CYCLE_WINDOW
        # Prune old entries
        log = [(t, d) for t, d in self._tx_log[node_id] if t >= window_start]
        self._tx_log[node_id] = log
        return sum(d for _, d in log)

    def get_utilization(self, node_id, current_time):
        """Get duty cycle utilization as fraction (0-1)."""
        used = self._get_airtime(node_id, current_time)
        return used / self.max_airtime if self.max_airtime > 0 else 0.0

    def reset(self):
        """Reset all tracking state."""
        self._tx_log.clear()
        self.violations = 0


class CollisionModel:
    """Models LoRa channel collisions.

    Two transmissions collide if they overlap in time and the receiver
    is within range of both transmitters. LoRa can survive some collisions
    due to the capture effect (stronger signal wins if >6dB difference).
    """

    CAPTURE_THRESHOLD_DB = 6.0  # dB difference needed for capture effect

    def __init__(self):
        # Active transmissions: list of (start_time, end_time, tx_node_id, rssi_at_receiver)
        self._active_tx = []

    def check_collision(self, rx_node_id, tx_node_id, tx_rssi, tx_start, tx_end):
        """Check if a reception would collide with active transmissions.

        Args:
            rx_node_id: Receiving node
            tx_node_id: Transmitting node
            tx_rssi: RSSI of desired signal at receiver
            tx_start: Transmission start time
            tx_end: Transmission end time

        Returns:
            True if packet survives (no collision or capture effect wins)
        """
        # Prune old transmissions
        self._active_tx = [
            t for t in self._active_tx if t[1] >= tx_start
        ]

        for start, end, other_tx, other_rssi in self._active_tx:
            if other_tx == tx_node_id:
                continue
            # Check time overlap
            if tx_start < end and tx_end > start:
                # Collision! Check capture effect
                rssi_diff = tx_rssi - other_rssi
                if rssi_diff < self.CAPTURE_THRESHOLD_DB:
                    return False  # Collision destroys packet

        # Record this transmission
        self._active_tx.append((tx_start, tx_end, tx_node_id, tx_rssi))
        return True

    def reset(self):
        """Reset collision state."""
        self._active_tx.clear()
