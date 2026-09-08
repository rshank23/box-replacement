Sample MBox tree used for local development and demos.

Layout: <Study>/<Country>/<Site>/<Category>/<file>

| Path | Expected outcome |
| --- | --- |
| STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf | SUCCESS via EXACT rule |
| STUDY-001/US/SITE-101/Monitoring/monitoring-plan.pdf | SUCCESS via REGEX rule |
| STUDY-001/DE/SITE-102/Safety/sae-report.pdf | SUCCESS via STUDY_DEFAULT rule |
| STUDY-002/GB/SITE-201/Safety/sae-2024-001.pdf | SAM_PENDING (classification not in picklist) |
| STUDY-003/JP/SITE-301/Site Management/site-docs.zip | SUCCESS - nested ZIP, extracted at all levels |
| STUDY-003/JP/SITE-301/Site Management/corrupt.zip | EXCEPTION - CORRUPT_ARCHIVE |
| STUDY-004/US/SITE-101/Trial Management/orphan.pdf | EXCEPTION - NO_MAPPING |

The two ZIP fixtures are binary and therefore generated rather than committed:

    python scripts/make_demo_data.py
