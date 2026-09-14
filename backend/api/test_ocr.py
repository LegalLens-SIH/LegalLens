from backend.api.ocr import _select_validation_profile


def test_default_category_selects_retail_profile():
    """Existing e-commerce/retail behavior is unchanged by the wholesale
    wiring added alongside it."""
    assert _select_validation_profile("General") == ("e_commerce_product_listing", False)
    assert _select_validation_profile("") == ("e_commerce_product_listing", False)
    assert _select_validation_profile("Food & Beverage") == ("e_commerce_product_listing", False)


def test_instrument_category_selects_pos_hardware_profile_unchanged():
    """Pre-existing branch (not part of this task) - confirms it still
    works exactly as before after the wholesale branch was added next to
    it, and never forces package_type to wholesale."""
    for value in ("weighing_instrument", "Instrument", "POS Hardware", "  pos hardware  "):
        assert _select_validation_profile(value) == ("pos_hardware_audit", False)


def test_wholesale_category_selects_wholesale_profile():
    for value in ("wholesale", "Wholesale", "wholesale_package", "wholesale package", "  WHOLESALE  "):
        assert _select_validation_profile(value) == ("wholesale_package", True)
