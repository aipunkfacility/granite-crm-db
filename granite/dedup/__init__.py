from granite.dedup.merger import merge_cluster, generate_conflicts_md
from granite.dedup.name_matcher import find_name_matches
from granite.dedup.phone_cluster import cluster_by_phones
from granite.dedup.site_matcher import cluster_by_site
from granite.dedup.validator import (
    validate_website,
    validate_phone,
    validate_phones,
    validate_email,
    validate_emails,
)

__all__ = [
    "merge_cluster",
    "generate_conflicts_md",
    "find_name_matches",
    "cluster_by_phones",
    "cluster_by_site",
    "validate_website",
    "validate_phone",
    "validate_phones",
    "validate_email",
    "validate_emails",
]
