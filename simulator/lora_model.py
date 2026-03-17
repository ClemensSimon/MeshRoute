"""
LoRa radio model for MeshRoute simulator.
Models path loss, RSSI, SNR, packet success rate, and time-on-air for EU 868MHz LoRa.
"""

import math

# EU 868MHz regulations
DUTY_CYCLE = 0.01  # 1% duty cycle limit
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


def path_loss(distance, frequency=FREQUENCY):
    """Log-distance path loss model.

    Args:
        distance: Distance in meters (must be > 0)
        frequency: Carrier frequency in Hz

    Returns:
        Path loss in dB
    """
    if distance <= 0:
        return 0.0
    if distance < PL_D0:
        distance = PL_D0
    pl_ref = 20 * math.log10(4 * math.pi * PL_D0 * frequency / 3e8)
    return pl_ref + 10 * PL_N * math.log10(distance / PL_D0)


def rssi_from_distance(distance, tx_power=TX_POWER):
    """Calculate received signal strength from distance.

    Args:
        distance: Distance in meters
        tx_power: Transmit power in dBm

    Returns:
        RSSI in dBm
    """
    return tx_power - path_loss(distance)


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
    """Calculate maximum theoretical range.

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


def link_quality_from_distance(distance, tx_power=TX_POWER):
    """Compute a 0-1 link quality score from distance.

    Args:
        distance: Distance in meters
        tx_power: Transmit power in dBm

    Returns:
        Quality score (0.0 to 1.0)
    """
    rssi = rssi_from_distance(distance, tx_power)
    return packet_success_rate(rssi)
