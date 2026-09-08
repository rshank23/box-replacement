from __future__ import annotations

from app.libs.common.path_parser import parse_path


def test_parses_study_country_site_category():
    info = parse_path("STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf")
    assert info.study == "STUDY-001"
    assert info.country == "US"
    assert info.site == "SITE-101"
    assert info.category == "Trial Management"
    assert info.file_name == "tmf-plan.pdf"
    assert info.extension == ".pdf"
    assert info.depth == 4
    assert info.folder_path == "STUDY-001/US/SITE-101/Trial Management"


def test_strips_root_prefix_and_backslashes():
    info = parse_path("/data/mbox/STUDY-001\\DE\\SITE-102\\Safety\\sae.pdf", "/data/mbox")
    assert info.relative_path == "STUDY-001/DE/SITE-102/Safety/sae.pdf"
    assert info.country == "DE"


def test_archive_member_inherits_hierarchy():
    info = parse_path("STUDY-003/JP/SITE-301/Site Management/site-docs.zip!/level-2/inner.zip!/sheet.pdf")
    assert info.study == "STUDY-003"
    assert info.country == "JP"
    assert info.site == "SITE-301"
    assert info.category == "Site Management"
    assert info.file_name == "sheet.pdf"
    assert info.is_inside_archive
    assert info.archive_chain == ("site-docs.zip", "inner.zip")


def test_shallow_path_yields_no_hierarchy():
    info = parse_path("orphan.pdf")
    assert info.study is None
    assert info.depth == 0
