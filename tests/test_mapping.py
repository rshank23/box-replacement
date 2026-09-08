from __future__ import annotations

from app.libs.common.models import MappingRule
from app.libs.common.path_parser import parse_path
from app.services.integration_api.domain.mapping import MappingEngine


def _rule(**kwargs) -> MappingRule:
    defaults = dict(mapping_id=kwargs.pop("mapping_id", 1), priority=100, is_active=True, default_metadata={})
    return MappingRule(**defaults, **kwargs)


def test_exact_wins_over_regex_and_study_default():
    engine = MappingEngine(
        [
            _rule(mapping_id=3, source_pattern="STUDY-001", match_type="STUDY_DEFAULT", document_type="Default"),
            _rule(mapping_id=2, source_pattern=".*/Trial Management/.*", match_type="REGEX", document_type="Regex"),
            _rule(
                mapping_id=1,
                source_pattern="STUDY-001/US/SITE-101/Trial Management",
                match_type="EXACT",
                document_type="Exact",
            ),
        ]
    )
    result = engine.resolve(parse_path("STUDY-001/US/SITE-101/Trial Management/a.pdf"))
    assert result.rule_id == 1
    assert result.metadata["document_type"] == "Exact"


def test_regex_wins_over_study_default():
    engine = MappingEngine(
        [
            _rule(mapping_id=3, source_pattern="STUDY-001", match_type="STUDY_DEFAULT", document_type="Default"),
            _rule(mapping_id=2, source_pattern=".*/Monitoring/.*", match_type="REGEX", document_type="Regex"),
        ]
    )
    result = engine.resolve(parse_path("STUDY-001/US/SITE-101/Monitoring/m.pdf"))
    assert result.match_type == "REGEX"


def test_global_default_is_last_resort():
    engine = MappingEngine(
        [_rule(mapping_id=9, source_pattern="*", match_type="GLOBAL_DEFAULT", document_type="Global")]
    )
    result = engine.resolve(parse_path("STUDY-XYZ/US/SITE-1/Cat/a.pdf"))
    assert result.matched
    assert result.metadata["document_type"] == "Global"


def test_no_match_returns_unmatched_result_with_folder_metadata():
    engine = MappingEngine([_rule(source_pattern="STUDY-999", match_type="STUDY_DEFAULT")])
    result = engine.resolve(parse_path("STUDY-001/US/SITE-101/Trial Management/a.pdf"))
    assert not result.matched
    assert result.metadata["study"] == "STUDY-001"


def test_folder_hierarchy_fills_gaps_and_override_wins():
    engine = MappingEngine(
        [
            _rule(
                source_pattern="STUDY-001",
                match_type="STUDY_DEFAULT",
                document_type="Trial Management",
                classification="Essential Document",
            )
        ]
    )
    result = engine.resolve(
        parse_path("STUDY-001/US/SITE-101/Trial Management/a.pdf"),
        overrides={"document_subtype": "Monitoring Plan", "target_site": "SITE-999"},
    )
    assert result.metadata["country"] == "US"
    assert result.metadata["site"] == "SITE-999"
    assert result.metadata["document_subtype"] == "Monitoring Plan"


def test_invalid_regex_does_not_raise():
    engine = MappingEngine([_rule(source_pattern="([unclosed", match_type="REGEX")])
    assert not engine.resolve(parse_path("A/B/C/D/a.pdf")).matched
