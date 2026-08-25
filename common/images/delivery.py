_BASE_OPTIONS = {"secure": True, "quality": "auto", "fetch_format": "auto"}

VARIANTS = {
    "thumbnail": {"width": 200, "height": 200, "crop": "fill", "gravity": "auto"},
    "card": {"width": 800, "crop": "limit"},
    "full": {"width": 1600, "crop": "limit"},
}


def build_url(resource, variant="full", *, signed=False):
    if not resource:
        return None
    options = dict(_BASE_OPTIONS)
    options.update(VARIANTS.get(variant, VARIANTS["full"]))
    if signed:
        options["sign_url"] = True
    return resource.build_url(**options)
