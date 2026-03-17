"""
Geohash encoding for MeshRoute simulator.
Simple base32 geohash implementation for clustering nodes by geographic proximity.
"""

BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat, lon, precision=6):
    """Encode latitude/longitude into a geohash string.

    Args:
        lat: Latitude (-90 to 90)
        lon: Longitude (-180 to 180)
        precision: Number of characters in the geohash (default 6)

    Returns:
        Geohash string of given precision
    """
    lat_range = (-90.0, 90.0)
    lon_range = (-180.0, 180.0)
    geohash = []
    bits = 0
    char_idx = 0
    is_lon = True

    while len(geohash) < precision:
        if is_lon:
            mid = (lon_range[0] + lon_range[1]) / 2.0
            if lon >= mid:
                char_idx = (char_idx << 1) | 1
                lon_range = (mid, lon_range[1])
            else:
                char_idx = char_idx << 1
                lon_range = (lon_range[0], mid)
        else:
            mid = (lat_range[0] + lat_range[1]) / 2.0
            if lat >= mid:
                char_idx = (char_idx << 1) | 1
                lat_range = (mid, lat_range[1])
            else:
                char_idx = char_idx << 1
                lat_range = (lat_range[0], mid)

        is_lon = not is_lon
        bits += 1

        if bits == 5:
            geohash.append(BASE32[char_idx])
            bits = 0
            char_idx = 0

    return "".join(geohash)


def encode_xy(x, y, area_size, precision=6):
    """Encode x,y meter coordinates to geohash.

    Maps simulation coordinates to a small geographic area for clustering.
    We map the simulation area to a 0.01 degree box near (48.0, 11.0) (Munich area).

    Args:
        x: X position in meters
        y: Y position in meters
        area_size: Size of the simulation area in meters
        precision: Geohash precision

    Returns:
        Geohash string
    """
    base_lat = 48.0
    base_lon = 11.0
    # Map area to ~0.01 degree range (about 1km)
    scale = 0.01 / max(area_size, 1)
    lat = base_lat + y * scale
    lon = base_lon + x * scale
    return encode(lat, lon, precision)


def common_prefix(a, b):
    """Return the common prefix of two geohash strings."""
    prefix = []
    for ca, cb in zip(a, b):
        if ca == cb:
            prefix.append(ca)
        else:
            break
    return "".join(prefix)
