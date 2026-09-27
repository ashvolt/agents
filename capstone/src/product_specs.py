"""The product requirement table.

**Invented, not sourced from a real print operation.** Stated in brief.md S9 as a known
limitation. The numbers are plausible for the class of product but they are assumptions,
and the eval set is generated against these same numbers — see research.md D-4 for why
that is a real constraint on what the evals can prove.

Constitution Principle: a check never hardcodes a threshold. It asks this table.
"""

from __future__ import annotations

from capstone.src.schemas import ProductSpec

# --------------------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------------------

_SPECS: dict[str, ProductSpec] = {
    spec.product_id: spec
    for spec in (
        ProductSpec(
            product_id="die-cut-sticker",
            display_name="Die-cut sticker",
            min_dpi=150,
            bleed_in=0.125,
            safe_zone_in=0.125,
            min_text_pt=6.0,
            min_stroke_pt=0.5,
            min_contrast_delta_e=15.0,
            accepted_color_modes=("CMYK",),
            allows_transparency=True,  # die-cut shape is defined by the cut line
            aspect_tolerance=0.02,
        ),
        ProductSpec(
            product_id="kiss-cut-sheet",
            display_name="Kiss-cut sticker sheet",
            min_dpi=150,
            bleed_in=0.125,
            safe_zone_in=0.1875,  # more keep-out: multiple cuts on one sheet
            min_text_pt=6.0,
            min_stroke_pt=0.5,
            min_contrast_delta_e=15.0,
            accepted_color_modes=("CMYK",),
            allows_transparency=True,
            aspect_tolerance=0.02,
        ),
        ProductSpec(
            product_id="vinyl-banner",
            display_name="Vinyl banner",
            min_dpi=72,  # viewed from distance, lower DPI is acceptable
            bleed_in=0.25,
            safe_zone_in=0.5,  # hem and grommets eat the edge
            min_text_pt=24.0,  # read from far away
            min_stroke_pt=2.0,
            min_contrast_delta_e=25.0,  # outdoor viewing, harsher light
            accepted_color_modes=("CMYK",),
            allows_transparency=False,  # substrate is opaque
            aspect_tolerance=0.05,
        ),
        ProductSpec(
            product_id="roll-label",
            display_name="Roll label",
            min_dpi=300,  # small format, held close
            bleed_in=0.0625,
            safe_zone_in=0.0625,
            min_text_pt=5.0,
            min_stroke_pt=0.35,
            min_contrast_delta_e=12.0,
            accepted_color_modes=("CMYK",),
            allows_transparency=False,
            aspect_tolerance=0.01,  # tight registration on a roll
        ),
        ProductSpec(
            product_id="custom-magnet",
            display_name="Custom magnet",
            min_dpi=200,
            bleed_in=0.125,
            safe_zone_in=0.125,
            min_text_pt=8.0,
            min_stroke_pt=0.75,
            min_contrast_delta_e=18.0,
            accepted_color_modes=("CMYK",),
            allows_transparency=False,  # printed on opaque magnetic stock
            aspect_tolerance=0.02,
        ),
    )
}


class UnknownProductError(KeyError):
    """Raised for a product with no spec entry.

    Deliberately not a fallback. A guessed spec is a silent false approve waiting to
    happen: approve against a 72 DPI banner threshold what should have been checked
    against a 300 DPI label threshold and the press finds out, not the agent.
    Callers catch this and escalate with UNSUPPORTED_INPUT.
    """


def get_spec(product_id: str) -> ProductSpec:
    """Requirements for one product, or raise.

    Never returns a default. See UnknownProductError.
    """
    try:
        return _SPECS[product_id]
    except KeyError:
        known = ", ".join(sorted(_SPECS))
        raise UnknownProductError(
            f"no product spec for {product_id!r}. Known products: {known}"
        ) from None


def all_product_ids() -> tuple[str, ...]:
    return tuple(sorted(_SPECS))


def all_specs() -> tuple[ProductSpec, ...]:
    return tuple(_SPECS[pid] for pid in all_product_ids())


__all__ = ["UnknownProductError", "all_product_ids", "all_specs", "get_spec"]
