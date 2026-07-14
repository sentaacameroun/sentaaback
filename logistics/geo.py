import math


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance à vol d'oiseau en km. Pas de PostGIS (décision géoloc déjà prise) : suffisant
    pour classer des coursiers par proximité à cette échelle, pas pour des requêtes spatiales
    complexes."""
    lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))
